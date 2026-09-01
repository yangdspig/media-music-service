"""QQ 音乐专辑元数据客户端：公开网页接口（免登录）。

实测要点（2026-09-01）：
- 搜索 GET c.y.qq.com/soso/fcgi-bin/client_search_cp（t=8 专辑），需 Referer: y.qq.com；
- 详情走 u.y.qq.com/cgi-bin/musicu.fcg 网关（POST JSON，req_1.module/method/param）：
  GetAlbumDetail 取 basicInfo（albumName/publishDate/desc）与 singer.singerList（艺人），
  GetAlbumSongList 取曲目表（songInfo.title / interval 秒 / index_album 序号 / belongCD 碟号可空），
  albumID 传 0 即可（实测可用）；
- 封面按 albumMid 拼 T002R800x800M000 高清 URL。
"""
from __future__ import annotations

import httpx

from .schemas import AlbumInfo, AlbumSummary, AlbumTrack

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"

_HEADERS = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0"}
_TIMEOUT = httpx.Timeout(10.0)


def _cover_url(album_mid: str | None) -> str | None:
    return f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{album_mid}.jpg" if album_mid else None


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """按专辑名（可叠加艺人）搜索专辑。"""
    term = f"{artist} {keyword}".strip() if artist else keyword
    r = httpx.get(SEARCH_URL, params={"t": 8, "w": term, "format": "json", "n": limit},
                  headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    items = ((r.json().get("data") or {}).get("album") or {}).get("list") or []
    out = []
    for a in items:
        mid = a.get("albumMID")
        if not mid:
            continue
        out.append(AlbumSummary(
            collection_id=f"qq:{mid}",
            title=a.get("albumName") or "未知专辑",
            artists=[a["singerName"]] if a.get("singerName") else [],
            release_date=a.get("publicTime"),
            track_count=a.get("song_count") or 0,
            cover_url=_cover_url(mid),
            meta_source="qq",
        ))
    return out


def _musicu(module: str, method: str, param: dict) -> dict:
    """调 musicu.fcg 网关，返回 req_1.data；接口错误抛 LookupError。"""
    payload = {"comm": {"ct": 24, "cv": 0},
               "req_1": {"module": module, "method": method, "param": param}}
    r = httpx.post(MUSICU_URL, json=payload, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    req = r.json().get("req_1") or {}
    if req.get("code") != 0:
        raise LookupError(f"QQ 接口返回错误（{method}, code={req.get('code')}）")
    return req.get("data") or {}


def get_album(album_mid: str) -> AlbumInfo:
    """取专辑详情与曲目表；未找到/接口错误抛 LookupError，网络错误向上抛 httpx 异常。"""
    detail = _musicu("music.musichallAlbum.AlbumInfoServer", "GetAlbumDetail", {"albumMid": album_mid})
    bi = detail.get("basicInfo") or {}
    if not bi.get("albumName"):
        raise LookupError(f"QQ 未找到专辑（albumMid={album_mid}）")
    song_data = _musicu("music.musichallAlbum.AlbumSongList", "GetAlbumSongList",
                        {"albumMid": album_mid, "albumID": 0, "begin": 0, "num": 100, "order": 2})
    tracks = sorted(
        (
            AlbumTrack(
                disc=int(s.get("belongCD") or 1),
                track=s.get("index_album") or i + 1,
                title=s.get("title") or s.get("name") or "未知",
                artists=[x["name"] for x in s.get("singer", []) if x.get("name")],
                duration_s=float(s["interval"]) if s.get("interval") else None,
            )
            for i, item in enumerate(song_data.get("songList") or [])
            for s in [item.get("songInfo") or item]
        ),
        key=lambda t: (t.disc, t.track),
    )
    artists = [x["name"] for x in (detail.get("singer") or {}).get("singerList", []) if x.get("name")]
    return AlbumInfo(
        collection_id=f"qq:{album_mid}",
        title=bi.get("albumName") or "未知专辑",
        artists=artists,
        release_date=bi.get("publishDate"),
        track_count=len(tracks),
        cover_url=_cover_url(album_mid),
        description=(bi.get("desc") or "").strip() or None,
        meta_source="qq",
        tracks=tracks,
    )
