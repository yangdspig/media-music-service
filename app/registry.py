"""源注册表：枚举 musicdl 全部源并附加元数据。

设计要点：
- 动态读取 MusicClientBuilder.REGISTERED_MODULES，保证 musicdl 升级新增源后自动可见；
- 元数据（分类/歌单支持/cookies 需求）静态维护，作为服务的"能力清单"暴露给客户端；
- 提供 build_client() 统一构造 musicdl MusicClient，并把 config.yaml 的 cookies 注入。
"""
from __future__ import annotations

from typing import Any

from musicdl.modules import MusicClientBuilder
from musicdl import musicdl as musicdl_pkg

from .config import settings

# ---- 静态元数据（基于 musicdl v2.13.4 官方文档口径） ----
_PLAYLIST_SOURCES = {
    "AppleMusicClient", "DeezerMusicClient", "FiveSingMusicClient", "JamendoMusicClient",
    "JooxMusicClient", "KuwoMusicClient", "KugouMusicClient", "MiguMusicClient",
    "NeteaseMusicClient", "QQMusicClient", "QianqianMusicClient", "QobuzMusicClient",
    "SoundCloudMusicClient", "StreetVoiceMusicClient", "SodaMusicClient", "SpotifyMusicClient",
    "TIDALMusicClient", "FMAMusicClient", "JioSaavnMusicClient", "BodianMusicClient",
    "SunoMusicClient", "MOOVMusicClient",
}
_NEEDS_COOKIES = {
    "QQMusicClient", "TIDALMusicClient", "MOOVMusicClient", "AppleMusicClient", "FMAMusicClient",
}
_NEEDS_QUARK = {"MituMusicClient", "BuguyyMusicClient", "YinyuedaoMusicClient", "GequbaoMusicClient"}

_CATEGORY_MAP: dict[str, str] = {}
for _n in ["QQMusicClient", "KugouMusicClient", "StreetVoiceMusicClient", "SodaMusicClient",
           "FiveSingMusicClient", "NeteaseMusicClient", "QianqianMusicClient", "MiguMusicClient",
           "KuwoMusicClient", "BilibiliMusicClient", "BodianMusicClient", "MOOVMusicClient"]:
    _CATEGORY_MAP[_n] = "china"
for _n in ["YouTubeMusicClient", "JooxMusicClient", "AppleMusicClient", "JamendoMusicClient",
           "SoundCloudMusicClient", "DeezerMusicClient", "QobuzMusicClient", "SpotifyMusicClient",
           "TIDALMusicClient", "FMAMusicClient", "JioSaavnMusicClient", "OpenGameArtMusicClient",
           "SunoMusicClient", "WikimediaCommonsMusicClient", "AudiusMusicClient"]:
    _CATEGORY_MAP[_n] = "global"
for _n in ["XimalayaMusicClient", "LizhiMusicClient", "QingtingMusicClient",
           "LRTSMusicClient", "ITunesMusicClient"]:
    _CATEGORY_MAP[_n] = "audiobook"
for _n in ["MP3JuiceMusicClient", "TuneHubMusicClient", "GDStudioMusicClient",
           "MyFreeMP3MusicClient", "JBSouMusicClient", "XiaoBaiMusicClient"]:
    _CATEGORY_MAP[_n] = "aggregator"
# 其余归入 thirdparty（第三方下载站）


def list_sources() -> list[dict[str, Any]]:
    """列出全部源及其能力与可用性。"""
    out = []
    for name in MusicClientBuilder.REGISTERED_MODULES:
        cfg = settings.sources.get(name)
        enabled = cfg.enabled if cfg else True
        needs_ck = name in _NEEDS_COOKIES
        needs_qk = name in _NEEDS_QUARK
        available, note = enabled, ""
        if not enabled:
            note = "已在配置中禁用"
        elif needs_ck and not (cfg and (cfg.search_cookies or cfg.download_cookies)):
            available, note = False, "需要登录 cookies，未配置"
        elif needs_qk and not (cfg and cfg.quark_cookies):
            available, note = False, "无损音质需夸克网盘 cookies，未配置"
        out.append({
            "name": name,
            "category": _CATEGORY_MAP.get(name, "thirdparty"),
            "supports_search": True,
            "supports_download": True,
            "supports_playlist": name in _PLAYLIST_SOURCES,
            "needs_cookies": needs_ck or needs_qk,
            "available": available,
            "note": note,
        })
    return out


def _build_init_cfg() -> dict[str, Any]:
    """把 config.yaml 中的 cookies/目录/线程等组装成 musicdl 的 init_music_clients_cfg。"""
    init_cfg: dict[str, Any] = {}
    for name, cfg in settings.sources.items():
        c: dict[str, Any] = {}
        if cfg.search_cookies:
            c["default_search_cookies"] = cfg.search_cookies
        if cfg.download_cookies:
            c["default_download_cookies"] = cfg.download_cookies
        if cfg.parse_cookies:
            c["default_parse_cookies"] = cfg.parse_cookies
        if cfg.quark_cookies:
            c["quark_parser_config"] = {"cookies": cfg.quark_cookies}
        c.update(cfg.extra or {})
        if c:
            init_cfg[name] = c
    return init_cfg


def build_client(sources: list[str] | None = None):
    """构造 musicdl MusicClient 实例。

    强制注入 work_dir=settings.download_root，保证 musicdl 的搜索/下载
    落盘到服务统一管理的目录，而不是其默认的 ./musicdl_outputs。
    """
    music_sources = sources or settings.default_sources
    # 过滤掉不可用源
    available = {s["name"] for s in list_sources() if s["available"]}
    music_sources = [s for s in music_sources if s in available]
    if not music_sources:
        music_sources = [s for s in settings.default_sources if s in available]
    init_cfg = _build_init_cfg()
    for s in music_sources:
        init_cfg.setdefault(s, {})
        init_cfg[s].setdefault("work_dir", settings.download_root)
        init_cfg[s].setdefault("disable_print", True)
    return musicdl_pkg.MusicClient(
        music_sources=music_sources,
        init_music_clients_cfg=init_cfg,
        clients_threadings={s: settings.num_threads for s in music_sources},
    )
