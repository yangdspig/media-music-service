"""专辑元数据编排层：iTunes 首选，网易云/QQ 回退与简介补充。

规则（对应 docs/superpowers/specs/2026-09-01-cn-album-meta-design.md）：
- collection_id 命名空间：无前缀=iTunes，netease:/qq: 前缀路由到对应中文源；
- search_albums：iTunes 无结果，或关键词含 CJK 而结果全不含 CJK（覆盖不足）时，
  依次回退网易云→QQ 补齐至 limit（首个非空来源即停）；
- get_album（iTunes id）：各 storefront 均无曲目时用「专辑名+艺人」在中文源找同专辑
  整体接管（含曲目表）；iTunes 命中曲目表时 best-effort 合并中文源的简介与中文显示名；
- 同专辑判定：标题与艺人归一化相似度均 ≥0.6；罗马音场景（标题无 CJK，相似度天然低）
  放宽为发行日期前 10 位 + 曲目数精确一致（中文源搜索已带艺人关键词收敛结果集）；
- 中文源一切失败仅记 warning 降级，主链路行为与纯 iTunes 时一致。
"""
from __future__ import annotations

import logging

import httpx

from . import itunes, netease_meta, qq_meta
from .album import _CJK_RE, _sim
from .schemas import AlbumInfo, AlbumSummary

log = logging.getLogger(__name__)

_CN_CLIENTS = (netease_meta, qq_meta)
_SIM_THRESHOLD = 0.6
_LOOKUP_TIMEOUT = httpx.Timeout(10.0)


def _split_id(collection_id: str) -> tuple[str | None, str]:
    """拆分带前缀的 collection_id；无前缀返回 (None, 原 id)。"""
    prefix, sep, rest = collection_id.partition(":")
    if sep and prefix in ("netease", "qq") and rest:
        return prefix, rest
    return None, collection_id


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """专辑搜索：iTunes 优先，覆盖不足时中文源补齐（iTunes 网络异常向上抛，与现状一致）。"""
    results = itunes.search_albums(keyword=keyword, artist=artist, limit=limit)
    cjk_query = bool(_CJK_RE.search(keyword or "") or _CJK_RE.search(artist or ""))
    need_cn = not results or (cjk_query and not any(_CJK_RE.search(r.title) for r in results))
    if need_cn and len(results) < limit:
        for client in _CN_CLIENTS:
            try:
                cn = client.search_albums(keyword, artist=artist, limit=limit - len(results))
            except Exception as e:
                log.warning("%s 专辑搜索失败（降级跳过）: %s", client.__name__, e)
                continue
            if cn:
                results = results + cn
                break
    return results


def get_album(collection_id: str) -> AlbumInfo:
    """专辑详情：按 id 前缀路由；iTunes id 走接管/补充逻辑。"""
    prefix, raw_id = _split_id(collection_id)
    if prefix == "netease":
        return netease_meta.get_album(raw_id)
    if prefix == "qq":
        return qq_meta.get_album(raw_id)
    try:
        album = itunes.get_album(raw_id)
    except LookupError:
        album = _cn_takeover(raw_id)
        if album is None:
            raise
        return album
    return _merge_cn_supplement(album)


def _itunes_summary(collection_id: str) -> AlbumSummary | None:
    """轻量 lookup 取 iTunes 专辑摘要（接管时用于拼中文源搜索关键词）。"""
    r = httpx.get(itunes.LOOKUP_URL, params={"id": collection_id, "country": "CN"},
                  timeout=_LOOKUP_TIMEOUT)
    r.raise_for_status()
    coll = next((i for i in r.json().get("results", [])
                 if i.get("wrapperType") == "collection"), None)
    return itunes._to_summary(coll) if coll else None


def _find_same_album(client, title: str, artist: str | None,
                     release_date: str | None = None, track_count: int = 0) -> AlbumInfo | None:
    """在中文源找同专辑并取详情；未找到/失败返回 None（降级）。"""
    try:
        cands = client.search_albums(title, artist=artist, limit=5)
    except Exception as e:
        log.warning("%s 专辑搜索失败（降级跳过）: %s", client.__name__, e)
        return None
    best, best_score = None, 0.0
    for c in cands:
        ts = _sim(title, c.title)
        asim = max((_sim(artist, a) for a in c.artists), default=0.0) if artist else 0.5
        score = 0.6 * ts + 0.4 * asim
        if ts >= _SIM_THRESHOLD and asim >= _SIM_THRESHOLD and score > best_score:
            best, best_score = c, score
    if best is None and not _CJK_RE.search(title or "") and release_date and track_count:
        # 罗马音场景：标题相似度天然为 0，放宽为发行日期+曲目数精确一致
        best = next((c for c in cands
                     if (c.release_date or "")[:10] == release_date[:10]
                     and c.track_count == track_count), None)
    if best is None:
        return None
    _, raw_id = _split_id(best.collection_id)
    try:
        return client.get_album(raw_id)
    except Exception as e:
        log.warning("%s 专辑详情获取失败（降级跳过）: %s", client.__name__, e)
        return None


def _cn_takeover(collection_id: str) -> AlbumInfo | None:
    """iTunes 各 storefront 无曲目时：中文源整体接管（含曲目表）。"""
    summary = _itunes_summary(collection_id)
    if summary is None:
        return None
    artist = summary.artists[0] if summary.artists else None
    for client in _CN_CLIENTS:
        album = _find_same_album(client, summary.title, artist,
                                 release_date=summary.release_date,
                                 track_count=summary.track_count)
        if album is not None and album.tracks:
            return album
    return None


def _merge_cn_supplement(album: AlbumInfo) -> AlbumInfo:
    """iTunes 命中时合并中文源简介与中文显示名（CJK 保护，命中一个中文源即停）。"""
    artist = album.artists[0] if album.artists else None
    for client in _CN_CLIENTS:
        cn = _find_same_album(client, album.title, artist,
                              release_date=album.release_date,
                              track_count=album.track_count)
        if cn is None:
            continue
        changed = False
        if cn.description and not album.description:
            album.description = cn.description
            changed = True
        if not _CJK_RE.search(album.title) and _CJK_RE.search(cn.title):
            album.title = cn.title
            changed = True
        if (album.artists and cn.artists
                and not _CJK_RE.search(album.artists[0]) and _CJK_RE.search(cn.artists[0])):
            album.artists = cn.artists
            changed = True
        if changed:
            album.meta_source = f"itunes+{cn.meta_source}"
        return album
    return album
