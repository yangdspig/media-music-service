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
import time

import httpx

from .album import _normalize, _sim, t2s
from .genre import normalize_genre
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


# ---- 艺人流派兜底：单曲归档与中文源专辑无 genre 时按艺人查 iTunes ----
# storefront 链（同 COUNTRY_CHAIN 思路，JP 对中文艺人意义小故省略）
_ARTIST_GENRE_CHAIN = ["CN", "HK", "TW", "US"]
_ARTIST_SIM_THRESHOLD = 0.6  # 艺人名相似度下限（实测：搜"阿杜"会命中"野狗阿杜"）
_ARTIST_GENRE_CACHE: dict[str, str | None] = {}  # 进程内缓存（含未命中的 None）


def _get_json_429_backoff(url: str, params: dict) -> dict:
    """GET + JSON；429 退避重试最多 2 次（25s 递增），其余错误直接抛。"""
    delay = 25.0
    for attempt in range(3):
        r = httpx.get(url, params=params, timeout=_TIMEOUT)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        if attempt == 2:
            r.raise_for_status()
        time.sleep(delay)
        delay += 25.0
    raise AssertionError("unreachable")


def _artist_name_acceptable(query: str, name: str | None) -> bool:
    """艺人名采纳判定：归一化相等直接采纳；纯包含关系拒绝（实测：_sim 的包含规则给
    "阿杜" vs "野狗阿杜" 打 0.9 虚高分，会错挂无关艺人流派）；其余要求 _sim ≥ 0.6。"""
    na, nb = _normalize(query), _normalize(name)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return False
    return _sim(query, name) >= _ARTIST_SIM_THRESHOLD


def get_artist_genre(artist: str) -> str | None:
    """按艺人名查 iTunes 流派（单曲/中文源专辑无 genre 时的兜底）。

    /search?entity=musicArtist 按 storefront 链 CN→HK→TW→US 逐个尝试；优先取归一化
    相等的结果，其次相似度 ≥0.6（排除包含关系，见 _artist_name_acceptable）且带
    primaryGenreName 的结果；结果 t2s 后过 normalize_genre。
    带进程内缓存；任何异常返回 None（绝不影响归档主流程）。
    """
    key = t2s(artist or "").strip()
    if not key:
        return None
    if key in _ARTIST_GENRE_CACHE:
        return _ARTIST_GENRE_CACHE[key]
    genre: str | None = None
    try:
        for country in _ARTIST_GENRE_CHAIN:
            data = _get_json_429_backoff(
                SEARCH_URL, params={"term": key, "entity": "musicArtist",
                                    "country": country, "limit": 5})
            results = [i for i in data.get("results", [])
                       if i.get("wrapperType") == "artist" and i.get("primaryGenreName")]
            hit = (next((i for i in results if _normalize(key) == _normalize(i.get("artistName"))), None)
                   or next((i for i in results
                            if _artist_name_acceptable(key, i.get("artistName"))), None))
            if hit:
                genre = normalize_genre(t2s(hit["primaryGenreName"]))
                break
    except Exception:
        genre = None
    _ARTIST_GENRE_CACHE[key] = genre
    return genre
