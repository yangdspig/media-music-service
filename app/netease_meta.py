"""网易云专辑元数据客户端：公开网页 API（免登录）。

实测要点（2026-09-01）：
- 搜索 POST /api/search/get（type=10 专辑），PC UA + Referer 即可；
- 详情 GET /api/v1/album/{id}，需移动端 UA + os/appver cookie 才返回完整数据：
  专辑简介在 album.description，曲目在顶层 songs（no=序号、cd=碟号字符串、dt=毫秒时长、ar=艺人）；
- publishTime 为东八区零点的毫秒时间戳，须按 UTC+8 转日期（用 UTC 会差一天）；
- 存在反爬限流（code -462），本模块将其视为未命中抛 LookupError，调用方负责降级。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .schemas import AlbumInfo, AlbumSummary, AlbumTrack

SEARCH_URL = "https://music.163.com/api/search/get"
ALBUM_URL = "https://music.163.com/api/v1/album/{id}"

_SEARCH_HEADERS = {
    "Referer": "https://music.163.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}
_DETAIL_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Cookie": "os=ios; appver=8.20.21",
}

_TIMEOUT = httpx.Timeout(10.0)
_CST = timezone(timedelta(hours=8))  # publishTime 为东八区零点


def _ms_to_date(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_CST).date().isoformat()


def _to_summary(a: dict, fallback_id: str | None = None) -> AlbumSummary:
    artist = a.get("artist") or {}
    album_id = a.get("id") or fallback_id
    return AlbumSummary(
        collection_id=f"netease:{album_id}",
        title=a.get("name") or "未知专辑",
        artists=[artist["name"]] if artist.get("name") else [],
        release_date=_ms_to_date(a.get("publishTime")),
        track_count=a.get("size") or 0,
        cover_url=a.get("picUrl"),
        description=(a.get("description") or "").strip() or None,
        meta_source="netease",
    )


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """按专辑名（可叠加艺人）搜索专辑。"""
    term = f"{artist} {keyword}".strip() if artist else keyword
    r = httpx.post(SEARCH_URL, data={"s": term, "type": 10, "limit": limit},
                   headers=_SEARCH_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    albums = (r.json().get("result") or {}).get("albums") or []
    return [_to_summary(a) for a in albums if a.get("id")]


def get_album(album_id: str) -> AlbumInfo:
    """取专辑详情与曲目表；未命中/限流时抛 LookupError，网络错误向上抛 httpx 异常。"""
    r = httpx.get(ALBUM_URL.format(id=album_id), headers=_DETAIL_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    a = data.get("album") or {}
    if not a.get("name"):
        raise LookupError(f"网易云未找到专辑（id={album_id}, code={data.get('code')}）")
    summary = _to_summary(a, fallback_id=album_id)
    songs = data.get("songs") or []
    tracks = sorted(
        (
            AlbumTrack(
                disc=int(s.get("cd") or 1),
                track=s.get("no") or i + 1,
                title=s.get("name") or "未知",
                artists=[x["name"] for x in s.get("ar", []) if x.get("name")],
                duration_s=round(s["dt"] / 1000, 1) if s.get("dt") else None,
            )
            for i, s in enumerate(songs)
        ),
        key=lambda t: (t.disc, t.track),
    )
    if tracks:
        summary.track_count = len(tracks)
    return AlbumInfo(**summary.model_dump(), tracks=tracks)
