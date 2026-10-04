"""榜单目录：QQ / 网易云排行榜浏览与曲目解析。

实测要点（2026-09-23）：
- 网易云排行榜本质是歌单：GET music.163.com/api/toplist（免登录，PC UA + Referer）返回全部榜单，
  id 即 playlist id，榜单详情复用 app.playlist.parse_playlist（全量解析，无法分页）；
- QQ 排行榜为独立体系：fcg_myqq_toplist.fcg 取目录（topList：id/topTitle/picUrl/listenCount），
  fcg_v8_toplist_cp.fcg 取详情（songlist[].data 与歌单条目同构，song_begin/song_num 可分页，
  单页上限 100）；
- 榜单详情接口只返回元数据不含下载地址，需逐曲解析：复用 QQMusicClient.parseplaylist 的
  逐曲模式（_parsewiththirdpartapis → _parsewithofficialapiv1），无下载地址的（VIP/付费/区域）
  自然被过滤，结果集即"可下载集"。
"""
from __future__ import annotations

from contextlib import suppress

import httpx

from .playlist import parse_playlist
from .registry import build_client
from .schemas import ChartSummary, Track
from .search import cache_tracks, normalize_song

SOURCES = ("qq", "netease")

QQ_LIST_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_myqq_toplist.fcg"
QQ_DETAIL_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg"
NETEASE_LIST_URL = "https://music.163.com/api/toplist"

_QQ_HEADERS = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0"}
_NE_HEADERS = {
    "Referer": "https://music.163.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}
_TIMEOUT = httpx.Timeout(10.0)
_QQ_PAGE_CAP = 100  # fcg_v8_toplist_cp 单页上限


def list_charts(source: str) -> list[ChartSummary]:
    """列出指定平台的官方排行榜目录；source 非 qq/netease 抛 ValueError。"""
    if source == "qq":
        return _qq_list_charts()
    if source == "netease":
        return _netease_list_charts()
    raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")


def _qq_list_charts() -> list[ChartSummary]:
    # format=json 必须显式传：缺省时 fcg 接口返回 JSONP（MusicJsonCallback 包裹）无法直接解析
    r = httpx.get(QQ_LIST_URL, params={"format": "json"}, headers=_QQ_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise LookupError(f"QQ 榜单目录接口返回错误（code={data.get('code')}）")
    out = []
    for t in ((data.get("data") or {}).get("topList")) or []:
        if not t.get("id"):
            continue
        preview = [f"{s.get('singername', '')}-{s.get('songname', '')}"
                   for s in (t.get("songList") or [])[:3]]
        out.append(ChartSummary(
            id=str(t["id"]),
            source="qq",
            name=t.get("topTitle") or "未知榜单",
            cover_url=t.get("picUrl"),
            track_count=None,
            extra={"listen_count": t.get("listenCount"), "update": t.get("update"),
                   "preview": preview},
        ))
    return out


def _netease_list_charts() -> list[ChartSummary]:
    r = httpx.get(NETEASE_LIST_URL, headers=_NE_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise LookupError(f"网易云榜单目录接口返回错误（code={data.get('code')}），可能被限流，请稍后重试")
    out = []
    for t in data.get("list") or []:
        if not t.get("id"):
            continue
        out.append(ChartSummary(
            id=str(t["id"]),
            source="netease",
            name=t.get("name") or "未知榜单",
            cover_url=t.get("coverImgUrl"),
            track_count=None,
            extra={"update_frequency": t.get("updateFrequency"),
                   "description": (t.get("description") or "").strip() or None},
        ))
    return out


def get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    """取榜单曲目（标准化 Track，含下载地址，已落缓存可直接 submit_download）。

    QQ 侧 song_num 单页上限 100，limit 缺省/超出均按 100；网易云为全量解析后截断。
    逐曲解析失败的条目跳过，不阻断整榜。
    """
    if source == "qq":
        return _qq_chart_tracks(chart_id, limit)
    if source == "netease":
        return _netease_chart_tracks(chart_id, limit)
    raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")


def _qq_chart_tracks(chart_id: str, limit: int | None) -> list[Track]:
    song_num = min(limit, _QQ_PAGE_CAP) if limit else _QQ_PAGE_CAP
    r = httpx.get(QQ_DETAIL_URL, params={"topid": chart_id, "tpl": 3, "page": "detail",
                                         "type": "top", "song_begin": 0, "song_num": song_num,
                                         "format": "json"},
                  headers=_QQ_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise LookupError(f"QQ 榜单详情接口返回错误（topid={chart_id}, code={data.get('code')}）")
    client = build_client(["QQMusicClient"]).music_clients.get("QQMusicClient")
    if client is None:
        raise LookupError("QQ 源不可用（未配置 cookies 或登录凭证失效），无法解析榜单曲目下载地址")
    tracks: list[Track] = []
    for item in data.get("songlist") or []:
        sr = item.get("data") or item  # toplist_cp 条目为 {"data": {...}} 包裹，兼容裸条目
        try:
            song = _resolve_qq_track(client, sr)
        except Exception:
            continue  # 逐曲解析失败跳过，不阻断整榜（与 musicdl parseplaylist 口径一致）
        if song is None:
            continue
        t = normalize_song("QQMusicClient", song)
        if t:
            tracks.append(t)
    cache_tracks(tracks)  # 落缓存，submit_download 可仅按 id 提交
    return tracks


def _resolve_qq_track(client, search_result: dict):
    """复用 QQMusicClient.parseplaylist 的逐曲解析模式（musicdl qq.py:494-505）：
    thirdpart 先试（无 cookies 时可出无损），official 兜底；official 异常时回退 thirdpart 结果。
    两者都无有效下载地址返回 None（VIP/付费/区域限制），调用方跳过。"""
    song_info_flac = client._parsewiththirdpartapis(search_result=search_result)
    song_info = None
    with suppress(Exception):
        song_info = client._parsewithofficialapiv1(
            search_result=search_result, song_info_flac=song_info_flac,
            lossless_quality_is_sufficient=not bool(client.default_cookies))
    if song_info is not None and getattr(song_info, "with_valid_download_url", False):
        return song_info
    if song_info_flac is not None and getattr(song_info_flac, "with_valid_download_url", False):
        return song_info_flac
    return None


def _netease_chart_tracks(chart_id: str, limit: int | None) -> list[Track]:
    # 网易云榜单 id 即 playlist id，详情复用歌单解析（全量逐曲，无法分页，大榜单较慢）；
    # parse_playlist 内部已 cache_tracks。
    # 必须用 #/playlist?id= 片段形式：musicdl 从 URL fragment/path 提取 playlist id，
    # ?id= 查询串形式会误取为字面量 "playlist" 导致解析 0 首（2026-10-04 E2E 实测）。
    tracks = parse_playlist(url=f"https://music.163.com/#/playlist?id={chart_id}",
                            source="NeteaseMusicClient")
    return tracks[:limit] if limit else tracks
