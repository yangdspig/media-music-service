"""QQ 音乐登录态保活：musickey 自动刷新 + 过期检测。

实测依据（docs/superpowers/specs/2026-09-02-qq-cookie-keepalive-design.md）：
- 刷新接口 music.login.LoginServer/Login，expired_in/musicid 必须传 int（str → code=10006）
- 空 refresh_key 可刷新；响应下发的新 refresh_key 必须持久化到状态文件
- musickey 有效期 keyExpiresIn=259200（3 天）；access_token 约 60 天
- 状态文件优先级高于 config.yaml；config 仅作种子，用户重新粘贴（createtime 变化）自动重置
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from musicdl.modules.utils.qqutils import Device, QQMusicClientUtils

from .config import settings

logger = logging.getLogger(__name__)

QQ_SOURCE = "QQMusicClient"
_REFRESH_THRESHOLD_S = 24 * 3600   # 剩余有效期低于 24h 才刷新
_DEFAULT_KEY_EXPIRES_IN = 259200   # musickey 默认有效期 3 天（实测）
_FATAL_CODES = {1000, 104401, 104400}  # 凭证彻底失效，需重新粘贴 cookies
_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_CHECK_URL = "https://c6.y.qq.com/rsc/fcgi-bin/fcg_get_profile_homepage.fcg"
_APP_VERSION = "14.9.0.8"
_APP_CV = 14090008


class QQAuthRefreshError(Exception):
    """刷新失败（可重试）。code 为服务端业务码。"""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message or f"QQ 凭证刷新失败 code={code}")
        self.code = code


class QQAuthExpiredError(QQAuthRefreshError):
    """凭证彻底失效（refresh_token/access_token 过期），需用户重新粘贴 cookies。"""


@dataclass
class QQCredential:
    musicid: int = 0
    musickey: str = ""
    openid: str = ""
    unionid: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expired_at: int = 0          # access_token 过期时间戳
    refresh_key: str = ""
    login_type: int = 0
    musickey_createtime: int = 0
    key_expires_in: int = 0


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_cookie_str(raw: str) -> dict[str, str]:
    """'k1=v1; k2=v2' → dict；value 里允许含 '='。"""
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_credential(cookies: dict) -> QQCredential:
    """从 cookie dict 提取凭证字段（字段名兼容 musicdl fromcookiesdict 口径），int 修正。"""
    g = lambda *keys: next((str(cookies[k]) for k in keys if cookies.get(k)), "")
    return QQCredential(
        musicid=_int(cookies.get("musicid") or cookies.get("uin")),
        musickey=g("musickey", "qqmusic_key"),
        openid=g("openid", "psrf_qqopenid", "wxopenid"),
        unionid=g("unionid", "psrf_qqunionid", "wxunionid"),
        access_token=g("access_token", "psrf_qqaccess_token", "wxaccess_token"),
        refresh_token=g("refresh_token", "psrf_qqrefresh_token", "wxrefresh_token"),
        expired_at=_int(cookies.get("expired_at") or cookies.get("psrf_access_token_expiresAt")),
        refresh_key=g("refresh_key"),
        login_type=_int(cookies.get("tmeLoginType") or cookies.get("loginType")),
        musickey_createtime=_int(cookies.get("psrf_musickey_createtime")),
    )


def credential_to_cookies(cred: QQCredential, base: dict) -> dict:
    """把刷新后的凭证映射回 cookie dict：更新已知键，保留其余键。"""
    out = dict(base)
    for key in ("qqmusic_key", "qm_keyst"):
        if key in out:
            out[key] = cred.musickey
    out.setdefault("qqmusic_key", cred.musickey)
    if "psrf_qqaccess_token" in out:
        out["psrf_qqaccess_token"] = cred.access_token
    if "psrf_qqrefresh_token" in out:
        out["psrf_qqrefresh_token"] = cred.refresh_token
    if "psrf_access_token_expiresAt" in out:
        out["psrf_access_token_expiresAt"] = str(cred.expired_at)
    if "psrf_musickey_createtime" in out:
        out["psrf_musickey_createtime"] = str(cred.musickey_createtime)
    return out


# ---- 状态文件（与 db_path 同目录，容器内已持久化） ----

def _state_path() -> Path:
    return Path(settings.db_path).parent / "qq_auth_state.json"


def _load_state() -> dict | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_state(cred: QQCredential, config_createtime: str, expired: bool) -> None:
    payload = {"credential": asdict(cred), "refreshed_at": int(time.time()),
               "config_createtime": config_createtime, "expired": expired}
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _config_cookies() -> dict | None:
    cfg = settings.sources.get(QQ_SOURCE)
    raw = (cfg.search_cookies or cfg.download_cookies) if cfg else None
    if not raw:
        return None
    return parse_cookie_str(raw) if isinstance(raw, str) else dict(raw)


def _state_matches_config(state: dict, config_cookies: dict) -> bool:
    """状态文件是否仍对应当前 config 种子（用户重新粘贴 → createtime 变化 → 失配）。"""
    return state.get("config_createtime") == str(_int(config_cookies.get("psrf_musickey_createtime")))


def effective_cookies() -> dict | None:
    """状态文件有效 → 刷新后的完整 cookies；否则 None（调用方用 config 原值）。"""
    config = _config_cookies()
    if config is None:
        return None
    state = _load_state()
    if state and not state.get("expired") and _state_matches_config(state, config):
        cred = QQCredential(**state["credential"])
        return credential_to_cookies(cred, config)
    return None


def state_is_expired() -> bool:
    """凭证是否已被判定彻底失效（且用户尚未重新粘贴）。"""
    config = _config_cookies()
    state = _load_state()
    return bool(config is not None and state and state.get("expired")
                and _state_matches_config(state, config))
