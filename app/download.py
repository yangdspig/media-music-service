"""下载任务管理：内存任务表 + 后台线程执行 musicdl 同步下载。

设计取舍（按设计方案）：
- 不引入 Celery/Redis，用内存 dict + threading 即可满足内网自用；
- musicdl 的 download() 是同步阻塞的，放到后台线程跑，API 立即返回 task_id；
- 目录组织：download_root / [subdir 或 时间戳+关键词] / 歌曲文件。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import effective_max_size_mb, settings
from .registry import build_client
from .schemas import DownloadTask, DownloadTrackInput, TaskStatus, Track
from . import storage

logger = logging.getLogger(__name__)

_TASKS: dict[str, DownloadTask] = {}
_LOCK = threading.Lock()


def _task_to_dict(t: DownloadTask) -> dict[str, Any]:
    return t.model_dump()


def register_task(task: DownloadTask) -> None:
    """注册任务到内存表并落库（供单曲/专辑下载共用）。"""
    with _LOCK:
        _TASKS[task.task_id] = task
    storage.upsert_task(_task_to_dict(task))


def save_task(task: DownloadTask) -> None:
    """任务状态变更后落库。"""
    storage.upsert_task(_task_to_dict(task))


def download_songs(source: str, song_dicts: list[dict], save_dir: str) -> int:
    """用对应源的 client 下载一组歌曲（musicdl 同步阻塞调用）。

    song_dicts 为 musicdl 原始 SongInfo dict；带 `_save_path` 键时可覆盖落盘文件名。
    返回成功构建 SongInfo 的数量；构建失败或下载异常时抛错。
    注意：不要再用额外线程包裹，musicdl 的 rich.Progress 在嵌套线程里会死锁。
    """
    song_infos = _dicts_to_songinfos(song_dicts, save_dir)
    if not song_infos:
        raise RuntimeError("SongInfo 重建失败")
    client = build_client([source])
    client.download(song_infos=song_infos)
    return len(song_infos)


_AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ape", ".wav", ".alac", ".ogg", ".aac",
               ".dsf", ".dff", ".tak", ".tta", ".opus", ".wma"}


def _find_downloaded_file(save_dir: str, filename: str, identifier: str, before: set[str]) -> str | None:
    """下载后定位落盘文件：优先按指定文件名，其次按 identifier 匹配新增的音频文件。

    注意必须限定音频扩展名：musicdl 会同时落盘同名 .lrc 歌词，
    不限定时可能把歌词文件误当成音频返回。
    """
    if filename and (Path(save_dir) / filename).exists():
        return filename
    for p in Path(save_dir).iterdir():
        if (p.is_file() and p.suffix.lower() in _AUDIO_EXTS
                and p.name not in before and identifier and identifier in p.name):
            return p.name
    return None


def _resolve_tracks(inputs: list[DownloadTrackInput]) -> list[Track]:
    """把下载提交项解析为完整 Track：raw 非空直接用；否则按 id 查搜索缓存补全。"""
    from .search import get_cached
    resolved: list[Track] = []
    missing: list[str] = []
    for t in inputs:
        if t.raw:
            resolved.append(Track(**t.model_dump()))
            continue
        cached = get_cached(t.id)
        (resolved.append(cached) if cached else missing.append(t.id))
    if missing:
        raise ValueError("以下曲目缺少 raw 且未命中搜索缓存（请重新搜索后再提交下载）: " + ", ".join(missing))
    return resolved


def submit(tracks: list[DownloadTrackInput], subdir: str | None = None, library: str | None = None,
           max_size_mb: float | None = None) -> DownloadTask:
    if library:
        # 提前校验库名（白名单），避免下载完成后才发现归档目标不存在
        from .libraries import resolve_library_root
        resolve_library_root(library)
    tracks = _resolve_tracks(tracks)
    if not tracks:
        raise ValueError("tracks 不能为空")
    # 体积上限过滤：超限曲目直接拒绝下载（接口传参 >0 优先，否则用配置，0/空不限）
    limit_mb = effective_max_size_mb(max_size_mb)
    rejected: list[Track] = []
    if limit_mb:
        limit_bytes = limit_mb * 1024 * 1024
        rejected = [t for t in tracks if t.size_bytes and t.size_bytes > limit_bytes]
        tracks = [t for t in tracks if t not in rejected]
        if not tracks:
            raise ValueError(f"全部曲目体积超限（上限 {limit_mb:g}MB，拒绝 {len(rejected)} 首）")
    task_id = uuid.uuid4().hex[:12]
    # 落盘目录：根目录 / 子目录（默认 时间戳_首曲名）
    if not subdir:
        first = tracks[0].title if tracks else "batch"
        safe_first = "".join(c for c in first if c not in r'\/:*?"<>|')[:30]
        subdir = time.strftime("%Y%m%d-%H%M%S") + "_" + (safe_first or "batch")
    save_dir = str(Path(settings.download_root) / subdir)
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    task = DownloadTask(task_id=task_id, total=len(tracks), save_dir=save_dir, library=library)
    for t in rejected:
        task.errors.append(f"体积超限跳过: {t.title}（{(t.size_bytes or 0) / 1024 / 1024:.1f}MB > {limit_mb:g}MB）")
    register_task(task)

    th = threading.Thread(target=_run, args=(task, tracks), daemon=True)
    th.start()
    return task


def _dicts_to_songinfos(song_dicts: list[dict], save_dir: str) -> list[Any]:
    """把标准化 Track.raw dict 还原为 musicdl 的 SongInfo 对象，并把落盘路径指向 save_dir。"""
    from musicdl.modules.utils import SongInfo
    fields = set(SongInfo.__dataclass_fields__.keys())
    out = []
    for d in song_dicts:
        payload = {k: v for k, v in d.items() if k in fields}
        payload["work_dir"] = save_dir
        # save_path 留空，交给 musicdl 按其规则在 work_dir 下生成文件名
        try:
            if hasattr(SongInfo, "fromdict"):
                si = SongInfo.fromdict(payload)
            else:
                si = SongInfo(**payload)
            # 双保险：fromdict 可能不覆盖 work_dir
            try:
                si.work_dir = save_dir
            except Exception:
                pass
            out.append(si)
        except Exception:
            try:
                out.append(SongInfo(**payload))
            except Exception:
                continue
    return out


def _run(task: DownloadTask, tracks: list[Track]) -> None:
    task.status = TaskStatus.RUNNING
    save_task(task)
    save_dir = task.save_dir or settings.download_root
    # 按来源分组，逐组用对应 client 下载（musicdl 按 source 分发）
    groups: dict[str, list[dict]] = {}
    for t in tracks:
        groups.setdefault(t.source, []).append(t.raw)
    for source, song_dicts in groups.items():
        grp_tracks = [x for x in tracks if x.source == source]
        before = {p.name for p in Path(save_dir).iterdir()} if Path(save_dir).exists() else set()
        try:
            task.current = source
            download_songs(source, song_dicts, save_dir)
            task.completed += len(song_dicts)
            # 逐曲定位实际落盘文件（musicdl 单曲失败不抛异常，找不到时 file=None，归档跳过）
            for t in grp_tracks:
                fname = _find_downloaded_file(save_dir, "", str(t.raw.get("identifier", "")), before)
                task.results.append({
                    "source": source, "title": t.title, "artists": t.artists,
                    "album": t.album, "ext": t.ext, "cover_url": t.cover_url,
                    "artist_img_url": t.artist_img_url,
                    "file": fname, "save_dir": save_dir,
                })
                storage.record_file(task.task_id, t.model_dump(),
                                    save_path=str(Path(save_dir) / fname) if fname else save_dir)
        except Exception as e:  # 单源失败不拖垮整体
            # 完整堆栈进日志；errors 里带异常类型，避免 musicdl 抛出空消息异常时只剩 "None"
            logger.exception("下载失败 task=%s source=%s", task.task_id, source)
            task.failed += len(song_dicts)
            task.errors.append(f"{source}: {type(e).__name__}: {e}")
        save_task(task)
    task.current = None
    task.status = TaskStatus.SUCCESS if task.failed == 0 else (TaskStatus.FAILED if task.completed == 0 else TaskStatus.SUCCESS)
    task.message = f"完成 {task.completed}/{task.total}，失败 {task.failed}"
    # 指定了目标库时，下载完成后自动归档（单曲一步到位）
    if task.library:
        try:
            from .archive import archive_tracks  # 晚期 import 防循环（archive 依赖 download）
            res = archive_tracks(task.task_id, library=task.library)
            task.message += f"；自动归档[{task.library}] {res.status} {res.summary}"
            task.errors.extend(res.errors)
        except Exception as e:
            task.errors.append(f"自动归档失败: {e}")
            task.message += f"；自动归档失败: {e}"
    save_task(task)


def get(task_id: str) -> DownloadTask | None:
    with _LOCK:
        return _TASKS.get(task_id)


def cancel(task_id: str) -> bool:
    # M1 简化：musicdl 下载中断能力有限，仅做标记（未开始的批次不会启动）
    t = get(task_id)
    if not t:
        return False
    if t.status in (TaskStatus.PENDING,):
        t.status = TaskStatus.CANCELED
        storage.upsert_task(_task_to_dict(t))
        return True
    return False


def list_tasks(limit: int = 20) -> list[DownloadTask]:
    with _LOCK:
        return sorted(_TASKS.values(), key=lambda x: x.task_id, reverse=True)[:limit]
