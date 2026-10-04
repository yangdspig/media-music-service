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
    r = httpx.get(QQ_LIST_URL, headers=_QQ_HEADERS, timeout=_TIMEOUT)
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
    raise NotImplementedError  # Task 2 实现


def get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    """取榜单曲目（标准化 Track，含下载地址，已落缓存可直接 submit_download）。"""
    if source not in SOURCES:
        raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")
    raise NotImplementedError  # Task 3/4 实现
