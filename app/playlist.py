"""歌单解析：仅对 musicdl 支持歌单的源开放。"""
from __future__ import annotations

from typing import Any

from .registry import build_client
from .schemas import Track
from .search import cache_tracks, normalize_song


def parse_playlist(url: str, source: str | None = None) -> list[Track]:
    client = build_client([source] if source else None)
    song_infos = client.parseplaylist(url)
    tracks: list[Track] = []
    for item in song_infos or []:
        d = item if isinstance(item, dict) else getattr(item, "todict", lambda: {})()
        src = d.get("source") or source or "unknown"
        try:
            tracks.append(normalize_song(src, d))
        except Exception:
            continue
    cache_tracks(tracks)  # 落缓存，submit_download 可仅按 id 提交
    return tracks
