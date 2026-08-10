"""搜索服务：调用 musicdl 聚合并标准化为 Track。

对 musicdl 的 SongInfo dict 做防御性解析——不同源字段略有差异，
用 .get() 兜底，保证单个源字段缺失不会导致整体失败。
"""
from __future__ import annotations

import hashlib
from typing import Any

from .schemas import Track
from .registry import build_client


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
        cover_url=_first(d, "cover_url", "cover", "pic", "img"),
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
    return uniq, failed
