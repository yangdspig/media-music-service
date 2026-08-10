"""专辑级编排：逐曲匹配消歧 → 按序号命名落盘 → 输出 manifest.json。

设计要点（对应 ROADMAP M4-1 第一期）：
- 专辑元数据来自 itunes.py（iTunes 官方 API），下载仍走 musicdl 聚合源；
- 消歧打分：标题相似度为主，歌手/专辑/时长为辅；iTunes 各 storefront 可能返回
  繁体或罗马音，统一先做繁转简再比对；低于阈值宁可 unmatched 也不错配，
  分数与候选数写入 manifest 供人工/Agent 复核；
- 落盘文件名按官方曲目表序号生成（{disc}-{track:02d} 或 {track:02d} 前缀），
  通过 SongInfo._save_path 让 musicdl 使用指定文件名（spike 已验证生效）；
- 产物 manifest.json 替代解析私有 download_results.pkl，作为后续归档环节的输入契约。
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from opencc import OpenCC

from . import download as dl
from . import storage
from .config import settings
from .schemas import AlbumInfo, AlbumTrack, DownloadTask, TaskStatus, Track
from .search import search

_T2S = OpenCC("t2s")

ACCEPT_THRESHOLD = 0.6  # 低于此分数记为 unmatched，不强行下载
_MATCH_LIMIT = 10  # 每首曲目聚合搜索的候选上限
_DOWNLOAD_TIMEOUT = 30.0  # 封面下载超时


def t2s(s: str | None) -> str:
    """繁体转简体（iTunes HK/TW storefront 返回繁体曲目表）。"""
    return _T2S.convert(s) if s else ""


_PAREN_RE = re.compile(r"[\(（\[【][^\)）\]】]*[\)）\]】]")
_FEAT_RE = re.compile(r"feat\.?.*$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[\s\-·•_,，。.!！?？:：;；'\"’‘、/\\|~～]+")


def _normalize(s: str | None) -> str:
    """归一化用于比对：繁转简、小写、去括号补充（Live/Remix/feat. 等）、去标点空白。"""
    if not s:
        return ""
    s = _PAREN_RE.sub("", t2s(s).lower())
    s = _FEAT_RE.sub("", s)
    return _PUNCT_RE.sub("", s)


def _sim(a: str | None, b: str | None) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _artist_sim(expected: list[str], actual: list[str]) -> float:
    if not expected or not actual:
        return 0.5  # 任一侧缺信息时不作惩罚（中性分）
    return max((_sim(e, a) for e in expected for a in actual), default=0.0)


def _duration_sim(expected_s: Any, actual_s: Any) -> float:
    try:
        e, a = float(expected_s), float(actual_s)
    except (TypeError, ValueError):
        return 0.5  # 中性
    d = abs(e - a)
    if d <= 3:
        return 1.0
    if d <= 8:
        return 0.6
    if d <= 15:
        return 0.3
    return 0.0


def score_candidate(expected: AlbumTrack, album_title: str, cand: Track) -> float:
    """对一个候选 Track 打分（0~1）。权重：标题 0.45 / 时长 0.25 / 歌手 0.15 / 专辑 0.15。"""
    title = _sim(expected.title, cand.title)
    artist = _artist_sim(expected.artists, cand.artists)
    album_s = _sim(album_title, cand.album) if cand.album else 0.3
    dur = _duration_sim(expected.duration_s, cand.duration_s)
    return round(0.45 * title + 0.15 * artist + 0.15 * album_s + 0.25 * dur, 3)


def match_track(expected: AlbumTrack, album: AlbumInfo, sources: list[str] | None) -> dict[str, Any]:
    """对一首期望曲目做聚合搜索 + 打分消歧，返回匹配结论（含最高分候选，供 manifest）。"""
    keyword = t2s(expected.title)
    if expected.artists:
        keyword += " " + t2s(expected.artists[0])
    try:
        candidates, _failed = search(keyword=keyword.strip(), sources=sources, limit=_MATCH_LIMIT)
    except Exception as e:
        return {"status": "unmatched", "error": f"搜索失败: {e}", "match": None}
    if not candidates:
        return {"status": "unmatched", "error": "无候选结果", "match": None}
    scored = sorted(
        ((score_candidate(expected, album.title, c), c) for c in candidates),
        key=lambda x: x[0], reverse=True,
    )
    best_score, best = scored[0]
    match_info = {
        "source": best.source, "track_id": best.id, "title": best.title,
        "artists": best.artists, "album": best.album,
        "score": best_score, "candidates": len(candidates),
    }
    if best_score < ACCEPT_THRESHOLD:
        return {"status": "unmatched",
                "error": f"最高匹配分 {best_score} 低于阈值 {ACCEPT_THRESHOLD}",
                "match": match_info}
    return {"status": "matched", "match": match_info, "track": best}


def _safe_name(s: str) -> str:
    return "".join(c for c in s if c not in r'\/:*?"<>|').strip() or "未知"


def _track_filename(expected: AlbumTrack, multi_disc: bool, ext: str) -> str:
    prefix = f"{expected.disc}-{expected.track:02d}" if multi_disc else f"{expected.track:02d}"
    return f"{prefix} {_safe_name(t2s(expected.title))}.{ext.lstrip('.')}"


def _find_downloaded_file(save_dir: str, filename: str, identifier: str, before: set[str]) -> Optional[str]:
    """下载后定位落盘文件：优先按指定文件名，其次按 identifier 匹配新增文件。"""
    if filename and (Path(save_dir) / filename).exists():
        return filename
    for p in Path(save_dir).iterdir():
        if p.is_file() and p.name not in before and identifier and identifier in p.name:
            return p.name
    return None


def _download_cover(album: AlbumInfo, save_dir: str) -> Optional[str]:
    """下载高清封面到专辑目录，返回文件名；失败返回 None（不阻塞任务）。"""
    if not album.cover_url:
        return None
    try:
        r = httpx.get(album.cover_url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        suffix = ".png" if "png" in (r.headers.get("content-type") or "") else ".jpg"
        name = f"cover{suffix}"
        (Path(save_dir) / name).write_bytes(r.content)
        return name
    except Exception:
        return None


def submit_album_download(album: AlbumInfo, sources: list[str] | None = None, subdir: str | None = None) -> DownloadTask:
    """提交专辑下载任务（异步）：先逐曲匹配消歧，再按源分组下载，最后产出 manifest.json。"""
    task_id = uuid.uuid4().hex[:12]
    if not subdir:
        artist = _safe_name(t2s(album.artists[0])) if album.artists else "未知艺人"
        subdir = f"{artist} - {_safe_name(t2s(album.title))}"
    save_dir = str(Path(settings.download_root) / subdir)
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    task = DownloadTask(task_id=task_id, total=len(album.tracks), save_dir=save_dir)
    dl.register_task(task)
    threading.Thread(target=_run_album, args=(task, album, sources), daemon=True).start()
    return task


def _run_album(task: DownloadTask, album: AlbumInfo, sources: list[str] | None) -> None:
    task.status = TaskStatus.RUNNING
    dl.save_task(task)
    save_dir = task.save_dir or settings.download_root
    multi_disc = len({t.disc for t in album.tracks}) > 1
    entries: list[dict[str, Any]] = []
    to_download: list[tuple[dict[str, Any], AlbumTrack, Track, str]] = []

    # ---- 1) 逐曲匹配消歧 ----
    for expected in album.tracks:
        task.current = f"匹配: {expected.title}"
        r = match_track(expected, album, sources)
        entry: dict[str, Any] = {
            "disc": expected.disc, "track": expected.track,
            "title": expected.title, "artists": expected.artists,
            "duration_s": expected.duration_s,
            "status": r["status"], "match": r.get("match"),
            "file": None, "ext": None, "size_bytes": None, "error": r.get("error"),
        }
        if r["status"] == "matched":
            chosen: Track = r["track"]
            ext = (chosen.ext or "").lstrip(".")
            fname = _track_filename(expected, multi_disc, ext) if ext else ""
            entry["status"] = "pending"
            entry["ext"] = ext or None
            to_download.append((entry, expected, chosen, fname))
        entries.append(entry)
        dl.save_task(task)

    # ---- 2) 按源分组下载（复用 download 模块，_save_path 指定序号文件名）----
    groups: dict[str, list[int]] = {}
    for i, (_entry, _e, chosen, _f) in enumerate(to_download):
        groups.setdefault(chosen.source, []).append(i)
    for source, idxs in groups.items():
        task.current = f"下载: {source}"
        before = {p.name for p in Path(save_dir).iterdir()} if Path(save_dir).exists() else set()
        song_dicts = []
        for i in idxs:
            _entry, _expected, chosen, fname = to_download[i]
            raw = dict(chosen.raw)
            if fname:
                raw["_save_path"] = str(Path(save_dir) / fname)
            song_dicts.append(raw)
        try:
            dl.download_songs(source, song_dicts, save_dir)
        except Exception as e:  # 单源失败不拖垮整体，该源曲目统一记 failed
            for i in idxs:
                to_download[i][0]["status"] = "failed"
                to_download[i][0]["error"] = f"{source}: {e}"
            dl.save_task(task)
            continue
        # 逐曲核验落盘结果（musicdl 单曲失败不抛异常，需检查文件是否存在）
        for i in idxs:
            entry, expected, chosen, fname = to_download[i]
            found = _find_downloaded_file(save_dir, fname, str(chosen.raw.get("identifier", "")), before)
            if found:
                fpath = Path(save_dir) / found
                entry.update(status="ok", file=found,
                             size_bytes=fpath.stat().st_size if fpath.exists() else None)
                task.completed += 1
                task.results.append({"disc": expected.disc, "track": expected.track,
                                     "title": expected.title, "file": found, "source": source})
                storage.record_file(task.task_id, chosen.model_dump(), save_path=str(fpath))
            else:
                entry.update(status="failed", error="下载后未找到落盘文件")
        dl.save_task(task)

    # ---- 3) 封面 + manifest.json ----
    task.current = "产出清单"
    # 兜底：下载阶段后仍悬挂 pending 的条目统一置 failed
    for e in entries:
        if e["status"] == "pending":
            e.update(status="failed", error=e.get("error") or "下载未完成（未知原因）")
    cover = _download_cover(album, save_dir)
    unmatched = sum(1 for e in entries if e["status"] == "unmatched")
    failed = sum(1 for e in entries if e["status"] == "failed")
    ok = sum(1 for e in entries if e["status"] == "ok")
    task.failed = unmatched + failed
    manifest = {
        "task_id": task.task_id,
        "created_at": time.time(),
        "album": {**album.model_dump(exclude={"tracks"}), "meta_source": "itunes"},
        "cover": cover,
        "tracks": entries,
        "summary": {"total": len(entries), "ok": ok, "unmatched": unmatched, "failed": failed},
    }
    manifest_path = Path(save_dir) / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    task.manifest_path = str(manifest_path)

    task.current = None
    task.status = TaskStatus.SUCCESS if task.completed > 0 else (TaskStatus.FAILED if task.total > 0 else TaskStatus.SUCCESS)
    task.message = f"专辑《{album.title}》：成功 {ok}，未匹配 {unmatched}，下载失败 {failed}"
    dl.save_task(task)
