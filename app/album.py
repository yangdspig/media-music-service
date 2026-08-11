"""专辑级编排：逐曲匹配消歧 → 按序号命名落盘 → 输出 manifest.json。

设计要点（对应 ROADMAP M4-1 第一期）：
- 专辑元数据来自 itunes.py（iTunes 官方 API），下载仍走 musicdl 聚合源；
- 消歧打分：标题相似度为主，歌手/专辑/时长为辅；iTunes 各 storefront 可能返回
  繁体或罗马音，统一先做繁转简再比对；低于阈值宁可 unmatched 也不错配，
  分数与候选数写入 manifest 供人工/Agent 复核；
- 音质偏好：同分段（与最高分差 ≤0.1）候选中优先无损（flac 等），
  实在没有合格无损才选 MP3；分数明显更高的候选不受音质影响（见 pick_best）；
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
from .config import effective_max_size_mb, settings
from .schemas import AlbumInfo, AlbumTrack, DownloadTask, TaskStatus, Track
from .search import search

_T2S = OpenCC("t2s")

ACCEPT_THRESHOLD = 0.6  # 低于此分数记为 unmatched，不强行下载
_SCORE_TIE_MARGIN = 0.1  # 与最高分相差在此范围内视为"同分段"，此时音质优先于分数
_MATCH_LIMIT = 20  # 每首曲目聚合搜索的候选上限（五源结果波动大，上限放宽防正确候选被挤出）
_DOWNLOAD_TIMEOUT = 30.0  # 封面下载超时

_LOSSLESS_EXTS = {"flac", "ape", "wav", "alac", "tak", "tta", "dsd", "dff", "dsf"}


def quality_tier(cand: Track) -> int:
    """音质分档：3=无损，2=高码率有损（320k），1=其他。用于同分段候选的择优。"""
    ext = (cand.ext or "").lstrip(".").lower()
    quality = (cand.quality or "").lower()
    if ext in _LOSSLESS_EXTS or "lossless" in quality or "无损" in quality or "hires" in quality:
        return 3
    if "320" in quality:
        return 2
    return 1


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
    """对一个候选 Track 打分（0~1）。权重：标题 0.45 / 时长 0.25 / 歌手 0.15 / 专辑 0.15。

    版本标记惩罚：候选曲名含有期望曲名没有的版本标记（Mix/Remix/Dance/Live/伴奏等，
    含被归一化剥离的括号内容）时扣 0.2 分——防止括号剥离后 remix 与原版同分错配。
    """
    title = _sim(expected.title, cand.title)
    artist = _artist_sim(expected.artists, cand.artists)
    album_s = _sim(album_title, cand.album) if cand.album else 0.3
    dur = _duration_sim(expected.duration_s, cand.duration_s)
    base = 0.45 * title + 0.15 * artist + 0.15 * album_s + 0.25 * dur
    extra_markers = _version_markers(cand.title) - _version_markers(expected.title)
    if extra_markers:
        base -= 0.2
    return round(max(base, 0.0), 3)


_VERSION_MARKERS = ("remix", "mix", "dance", "live", "karaoke", "cover", "伴奏", "翻唱")


def _version_markers(s: str | None) -> set[str]:
    """提取曲名中的版本标记（繁转简小写后子串匹配）。"""
    low = t2s(s).lower()
    return {m for m in _VERSION_MARKERS if m in low}


def pick_best(scored: list[tuple[float, Track]]) -> tuple[float, Track]:
    """从已按分数降序排序的候选中选最优：分数最高的候选优先；
    与最高分相差不超过 _SCORE_TIE_MARGIN 的视为同分段，同分段内优先无损（tier 高者）。
    即"有合格的无损就下无损，实在没有才考虑 MP3"，但分数明显更高的候选不受音质影响。
    """
    top_score = scored[0][0]
    competitive = [(s, c) for s, c in scored if s >= top_score - _SCORE_TIE_MARGIN]
    return max(competitive, key=lambda x: (quality_tier(x[1]), x[0]))


def match_track(expected: AlbumTrack, album: AlbumInfo, sources: list[str] | None,
                max_size_mb: float | None = None) -> dict[str, Any]:
    """对一首期望曲目做聚合搜索 + 打分消歧，返回匹配结论（含最高分候选，供 manifest）。

    max_size_mb：单文件体积上限（接口传参 >0 优先，否则用配置 max_size_mb，0/空不限）。
    体积规则为"优先权"而非硬剔除：全部候选参与打分，优先选不超限且达阈值的最优者；
    无合格的不超限候选时才选超限最高分（≥阈值），manifest 标注 oversized_relaxed——
    宁可下超限文件也不让专辑缺曲或错配版本。
    """
    keyword = t2s(_PAREN_RE.sub("", expected.title or "")).strip() or t2s(expected.title)
    # 搜索关键词剔除括号补充（国语/[Album Version] 等）：平台搜索对括号内容敏感，
    # 带括号易引入无关结果、挤出正确候选；版本消歧交给打分阶段处理
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
    top_score = scored[0][0]

    # 体积统计（size_bytes 未知的按不超限放行）
    limit_mb = effective_max_size_mb(max_size_mb)
    limit_bytes = (limit_mb or 0) * 1024 * 1024

    def _oversized(c: Track) -> bool:
        return bool(limit_bytes and c.size_bytes and c.size_bytes > limit_bytes)

    oversized = sum(1 for _, c in scored if _oversized(c))

    def _match_info(score: float, c: Track, relaxed: bool) -> dict:
        return {
            "source": c.source, "track_id": c.id, "title": c.title,
            "artists": c.artists, "album": c.album, "ext": c.ext, "quality": c.quality,
            "quality_tier": quality_tier(c), "artist_img_url": c.artist_img_url,
            "score": score, "candidates": len(candidates), "oversized_filtered": oversized,
            "oversized_relaxed": relaxed,  # True = 无合格不超限候选，已放宽体积限制下载
        }

    qualified = [(s, c) for s, c in scored if s >= ACCEPT_THRESHOLD]
    if not qualified:
        return {"status": "unmatched",
                "error": f"最高匹配分 {top_score} 低于阈值 {ACCEPT_THRESHOLD}",
                "match": _match_info(top_score, scored[0][1], relaxed=False)}
    within = [(s, c) for s, c in qualified if not _oversized(c)]
    pool, relaxed = (within, False) if within else (qualified, True)
    best_score, best = pick_best(pool)
    return {"status": "matched", "match": _match_info(best_score, best, relaxed), "track": best}


_CJK_RE = re.compile(r"[一-鿿]")


def _majority_vote(values: list[str]) -> Optional[str]:
    """归一化多数表决：返回占比过半的值，否则 None。"""
    from collections import Counter
    votes = Counter(v for v in (t2s(x).strip() for x in values) if v)
    if not votes:
        return None
    top, count = votes.most_common(1)[0]
    return top if count > len(values) / 2 else None


def infer_display_names(album: dict, ok_entries: list[dict]) -> dict:
    """从匹配成功曲目的候选信息推断中文显示名（专辑名/艺人）。

    背景：iTunes 对部分老中文专辑只存罗马音专辑名（如 "Kou Shi Xin Fei"），
    但国内源候选里带着正确中文名。两道保护：
    - 多数表决占比须过半（防"范特西PLUS"式再版名噪音）；
    - 仅当 iTunes 原名不含 CJK 而表决结果含 CJK 时才替换（原名已对就不动）。
    返回可能含 display_title / display_artist 的 dict（可为空）。
    """
    if not ok_entries:
        return {}
    out: dict[str, str] = {}
    orig_title = album.get("title") or ""
    if not _CJK_RE.search(orig_title):
        voted = _majority_vote([(e.get("match") or {}).get("album") or "" for e in ok_entries])
        if voted and _CJK_RE.search(voted):
            out["display_title"] = voted
    orig_artist = t2s((album.get("artists") or [""])[0])
    if not _CJK_RE.search(orig_artist):
        voted = _majority_vote([((e.get("match") or {}).get("artists") or [""])[0] for e in ok_entries])
        if voted and _CJK_RE.search(voted):
            out["display_artist"] = voted
    return out


def _safe_name(s: str) -> str:
    return "".join(c for c in s if c not in r'\/:*?"<>|').strip() or "未知"


def _track_filename(expected: AlbumTrack, multi_disc: bool, ext: str) -> str:
    prefix = f"{expected.disc}-{expected.track:02d}" if multi_disc else f"{expected.track:02d}"
    return f"{prefix} {_safe_name(t2s(expected.title))}.{ext.lstrip('.')}"


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


def submit_album_download(album: AlbumInfo, sources: list[str] | None = None, subdir: str | None = None,
                          album_title: str | None = None, artist: str | None = None,
                          max_size_mb: float | None = None) -> DownloadTask:
    """提交专辑下载任务（异步）：先逐曲匹配消歧，再按源分组下载，最后产出 manifest.json。

    album_title/artist 为显式显示名覆盖（应对 iTunes 罗马音专辑名），
    会写入 manifest 供归档使用；subdir 命名也优先采用。
    max_size_mb 为单文件体积上限（MB），超限候选不参与匹配（>0 优先于配置，0/空不限）。
    """
    task_id = uuid.uuid4().hex[:12]
    if not subdir:
        artist_name = _safe_name(t2s(artist or (album.artists[0] if album.artists else "未知艺人")))
        title_name = _safe_name(t2s(album_title or album.title))
        subdir = f"{artist_name} - {title_name}"
    save_dir = str(Path(settings.download_root) / subdir)
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    task = DownloadTask(task_id=task_id, total=len(album.tracks), save_dir=save_dir)
    dl.register_task(task)
    display = {k: v for k, v in {"display_title": album_title, "display_artist": artist}.items() if v}
    threading.Thread(target=_run_album, args=(task, album, sources, display, max_size_mb), daemon=True).start()
    return task


def _run_album(task: DownloadTask, album: AlbumInfo, sources: list[str] | None,
               display: dict[str, str] | None = None, max_size_mb: float | None = None) -> None:
    task.status = TaskStatus.RUNNING
    dl.save_task(task)
    save_dir = task.save_dir or settings.download_root
    multi_disc = len({t.disc for t in album.tracks}) > 1
    entries: list[dict[str, Any]] = []
    to_download: list[tuple[dict[str, Any], AlbumTrack, Track, str]] = []

    # ---- 1) 逐曲匹配消歧 ----
    for expected in album.tracks:
        task.current = f"匹配: {expected.title}"
        r = match_track(expected, album, sources, max_size_mb=max_size_mb)
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
                to_download[i][0]["error"] = f"{source}: {type(e).__name__}: {e}"
            dl.save_task(task)
            continue
        # 逐曲核验落盘结果（musicdl 单曲失败不抛异常，需检查文件是否存在）
        for i in idxs:
            entry, expected, chosen, fname = to_download[i]
            found = dl._find_downloaded_file(save_dir, fname, str(chosen.raw.get("identifier", "")), before)
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
    album_dict = {**album.model_dump(exclude={"tracks"}), "meta_source": "itunes"}
    # 显示名：显式覆盖优先，否则从匹配候选自动推断（应对 iTunes 罗马音专辑名）
    display = display or infer_display_names(album_dict, [e for e in entries if e["status"] == "ok"])
    manifest = {
        "task_id": task.task_id,
        "created_at": time.time(),
        "album": {**album_dict, **display},
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
