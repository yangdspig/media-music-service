"""下载任务管理：内存任务表 + 后台线程执行 musicdl 同步下载。

设计取舍（按设计方案）：
- 不引入 Celery/Redis，用内存 dict + threading 即可满足内网自用；
- musicdl 的 download() 是同步阻塞的，放到后台线程跑，API 立即返回 task_id；
- 目录组织：download_root / [subdir 或 时间戳+关键词] / 歌曲文件。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import settings
from .registry import build_client
from .schemas import DownloadTask, TaskStatus, Track
from . import storage

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


def submit(tracks: list[Track], subdir: str | None = None) -> DownloadTask:
    task_id = uuid.uuid4().hex[:12]
    # 落盘目录：根目录 / 子目录（默认 时间戳_首曲名）
    if not subdir:
        first = tracks[0].title if tracks else "batch"
        safe_first = "".join(c for c in first if c not in r'\/:*?"<>|')[:30]
        subdir = time.strftime("%Y%m%d-%H%M%S") + "_" + (safe_first or "batch")
    save_dir = str(Path(settings.download_root) / subdir)
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    task = DownloadTask(task_id=task_id, total=len(tracks), save_dir=save_dir)
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
    # 按来源分组，逐组用对应 client 下载（musicdl 按 source 分发）
    groups: dict[str, list[dict]] = {}
    for t in tracks:
        groups.setdefault(t.source, []).append(t.raw)
    for source, song_dicts in groups.items():
        try:
            task.current = source
            download_songs(source, song_dicts, task.save_dir or settings.download_root)
            task.completed += len(song_dicts)
            for t in [x for x in tracks if x.source == source]:
                task.results.append({"source": source, "title": t.title, "artists": t.artists, "save_dir": task.save_dir})
                storage.record_file(task.task_id, t.model_dump(), save_path=task.save_dir or "")
        except Exception as e:  # 单源失败不拖垮整体
            task.failed += len(song_dicts)
            task.errors.append(f"{source}: {e}")
        save_task(task)
    task.current = None
    task.status = TaskStatus.SUCCESS if task.failed == 0 else (TaskStatus.FAILED if task.completed == 0 else TaskStatus.SUCCESS)
    task.message = f"完成 {task.completed}/{task.total}，失败 {task.failed}"
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
