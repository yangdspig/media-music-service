"""iTunes 专辑元数据客户端：Search/Lookup 官方 API（免 key）。

设计要点：
- 专辑搜索用 /search?entity=album；曲目表用 /lookup?id={collectionId}&entity=song；
- 不同 storefront 曲库差异大（如 CN 无此专、US 只有 collection 无曲目），
  lookup 按 COUNTRY_CHAIN 逐个尝试，取第一个返回曲目的 storefront；
- HK/TW storefront 返回繁体中文，US/JP 可能是罗马音，消歧在 album.py 统一处理；
- artworkUrl100 把尺寸段替换为 600x600 得高清封面。
"""
from __future__ import annotations

import re

import httpx

from .schemas import AlbumInfo, AlbumSummary, AlbumTrack

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"

# lookup 按序尝试的 storefront；CN 优先（命中时直接返回简体曲目表）
COUNTRY_CHAIN = ["CN", "HK", "TW", "US", "JP"]

_TIMEOUT = httpx.Timeout(15.0)


def _hi_res_cover(url: str | None) -> str | None:
    """把 iTunes 封面 URL 的尺寸段（100x100bb）替换为 600x600。"""
    if not url:
        return None
    return re.sub(r"\d+x\d+bb", "600x600bb", url)


def _to_summary(item: dict) -> AlbumSummary:
    return AlbumSummary(
        collection_id=str(item.get("collectionId", "")),
        title=item.get("collectionName") or "未知专辑",
        artists=[item["artistName"]] if item.get("artistName") else [],
        release_date=item.get("releaseDate"),
        track_count=item.get("trackCount") or 0,
        cover_url=_hi_res_cover(item.get("artworkUrl100")),
        genre=item.get("primaryGenreName"),
    )


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """按专辑名（可叠加艺人）搜索专辑。"""
    term = f"{artist} {keyword}".strip() if artist else keyword
    r = httpx.get(SEARCH_URL, params={"term": term, "entity": "album", "limit": limit}, timeout=_TIMEOUT)
    r.raise_for_status()
    return [_to_summary(i) for i in r.json().get("results", []) if i.get("collectionId")]


def get_album(collection_id: str) -> AlbumInfo:
    """取专辑详情与官方曲目表（按 storefront 链兜底）。

    找不到任何 storefront 有曲目时抛 LookupError，网络错误向上抛 httpx 异常。
    """
    last_count = 0
    for country in COUNTRY_CHAIN:
        r = httpx.get(LOOKUP_URL, params={"id": collection_id, "entity": "song", "country": country}, timeout=_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        last_count = len(results)
        songs = [i for i in results if i.get("wrapperType") == "track" and i.get("kind") == "song"]
        if songs:
            collection = next((i for i in results if i.get("wrapperType") == "collection"), None)
            break
    else:
        raise LookupError(f"iTunes 各 storefront 均无该专辑曲目（collection_id={collection_id}, 最后结果数={last_count}）")

    summary = _to_summary(collection) if collection else AlbumSummary(collection_id=str(collection_id), title="未知专辑")
    tracks = sorted(
        (
            AlbumTrack(
                disc=s.get("discNumber") or 1,
                track=s.get("trackNumber") or 0,
                title=s.get("trackName") or "未知",
                artists=[s["artistName"]] if s.get("artistName") else [],
                duration_s=round(s["trackTimeMillis"] / 1000, 1) if s.get("trackTimeMillis") else None,
            )
            for s in songs
        ),
        key=lambda t: (t.disc, t.track),
    )
    return AlbumInfo(**summary.model_dump(), tracks=tracks, storefront=country)
