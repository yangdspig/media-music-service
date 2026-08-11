"""搜索服务：调用 musicdl 聚合并标准化为 Track。

对 musicdl 的 SongInfo dict 做防御性解析——不同源字段略有差异，
用 .get() 兜底，保证单个源字段缺失不会导致整体失败。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from .schemas import Track
from .registry import build_client

# ---- 搜索结果缓存：下载提交只需回传 track id，服务端从这里补全 raw ----
# 背景：部分 Agent 客户端在 MCP 工具调用中无法可靠序列化/回传嵌套的 raw dict，
# 因此搜索/歌单结果落缓存，submit_download 接受仅含 id 的极简参数。
_TRACK_CACHE: dict[str, tuple[Track, float]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 3600  # 缓存 1 小时；过期需重新搜索


def cache_tracks(tracks: list[Track]) -> None:
    now = time.time()
    with _CACHE_LOCK:
        # 写入前顺手清理过期项，避免无界增长
        for k, (_, ts) in list(_TRACK_CACHE.items()):
            if now - ts > _CACHE_TTL_S:
                del _TRACK_CACHE[k]
        for t in tracks:
            _TRACK_CACHE[t.id] = (t, now)


def get_cached(track_id: str) -> Track | None:
    with _CACHE_LOCK:
        item = _TRACK_CACHE.get(track_id)
        if not item:
            return None
        t, ts = item
        if time.time() - ts > _CACHE_TTL_S:
            del _TRACK_CACHE[track_id]
            return None
        return t


def _first(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return default


def _song_to_dict(song: Any) -> dict:
    """兼容 musicdl 的 SongInfo dataclass 与普通 dict。"""
    if isinstance(song, dict):
        return song
    todict = getattr(song, "todict", None)
    if callable(todict):
        try:
            return todict()
        except Exception:
            pass
    try:
        import dataclasses
        if dataclasses.is_dataclass(song):
            return dataclasses.asdict(song)
    except Exception:
        pass
    return {}


def _kuwo_img_url(short: str | None, kind: str, size: int) -> str | None:
    """酷我图片相对路径拼完整 URL：kind 为 albumcover（专辑封面）或 starheads（艺人头像）；
    short 形如 "120/s4s76/56/xxx.jpg"，首段为尺寸，替换为目标尺寸；归档下载时另有尺寸/节点降级。"""
    if not short:
        return None
    path = str(short).lstrip("/")
    parts = path.split("/", 1)
    if len(parts) == 2 and parts[0].isdigit():
        path = parts[1]
    return f"https://img1.kuwo.cn/star/{kind}/{size}/{path}"


def _kuwo_cover_fallback(source: str, d: dict) -> str | None:
    """酷我源封面兜底：musicdl KuwoMusicClient 搜索不填 cover_url，
    但原始搜索数据带 web_albumpic_short，可拼 albumcover URL（500 尺寸优先）。"""
    if source != "KuwoMusicClient":
        return None
    return _kuwo_img_url(((d.get("raw_data") or {}).get("search") or {}).get("web_albumpic_short"),
                         "albumcover", 500)


def _artist_img_fallback(source: str, d: dict) -> str | None:
    """按源提取艺人头像 URL：酷我 web_artistpic_short（starheads/300）；咪咕 singerList[0].img。"""
    rd = d.get("raw_data") or {}
    if source == "KuwoMusicClient":
        return _kuwo_img_url((rd.get("search") or {}).get("web_artistpic_short"), "starheads", 300)
    if source == "MiguMusicClient":
        singers = (((rd.get("download") or {}).get("data") or {}).get("song") or {}).get("singerList") or []
        if isinstance(singers, list) and singers and isinstance(singers[0], dict):
            return singers[0].get("img")
    return None


def normalize_song(source: str, song: Any) -> Track | None:
    d = _song_to_dict(song)
    if not d:
        return None
    # 无有效下载地址的直接跳过（与 musicdl 内部过滤口径一致）
    if not d.get("download_url") and not d.get("downloaded_contents"):
        return None
    identifier = str(_first(d, "identifier", "id", "mid", "songid", default=""))
    if not identifier:
        identifier = hashlib.md5(repr(sorted(d.items())).encode()).hexdigest()[:16]
    singers = _first(d, "singers", "singer", "artists", "artist", default=[])
    if isinstance(singers, str):
        singers = [s.strip() for s in singers.replace("、", "/").split("/") if s.strip()]
    size_bytes = _first(d, "file_size_bytes", "size_bytes", "size", "file_size")
    if isinstance(size_bytes, str):
        try:
            size_bytes = int(float(size_bytes))
        except Exception:
            size_bytes = None
    return Track(
        id=f"{source}:{identifier}",
        source=source,
        title=str(_first(d, "song_name", "songname", "title", "name", default="未知")),
        artists=singers if isinstance(singers, list) else [str(singers)],
        album=_first(d, "album", "album_name"),
        duration_s=_first(d, "duration_s", "duration", "interval"),
        quality=_first(d, "bitrate", "quality", "quality_description"),
        ext=_first(d, "ext", "format"),
        size_bytes=size_bytes,
        cover_url=_first(d, "cover_url", "cover", "pic", "img") or _kuwo_cover_fallback(source, d),
        artist_img_url=_first(d, "artist_img_url", "artist_pic", "avatar") or _artist_img_fallback(source, d),
        lyric=_first(d, "lyric", "lyrics"),
        raw=d,
    )


def search(keyword: str, sources: list[str] | None = None, limit: int = 20) -> tuple[list[Track], list[str]]:
    """聚合多源搜索。返回 (tracks, failed_sources)。"""
    client = build_client(sources)
    failed: list[str] = []
    tracks: list[Track] = []
    try:
        results = client.search(keyword=keyword)  # dict[source_name, list[SongInfo]]
    except Exception:
        return [], sources or []
    for source, items in (results or {}).items():
        try:
            for song in items or []:
                t = normalize_song(source, song)
                if t:
                    tracks.append(t)
        except Exception:
            failed.append(source)
    # 去重（同源同 id）并截断
    seen, uniq = set(), []
    for t in tracks:
        if t.id in seen:
            continue
        seen.add(t.id)
        uniq.append(t)
        if len(uniq) >= limit:
            break
    cache_tracks(uniq)  # 落缓存，submit_download 可仅按 id 提交
    return uniq, failed
