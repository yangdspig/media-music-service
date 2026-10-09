"""内存中的扫码登录会话；独立实现平台协议，凭证只在服务端保存。

QQ 使用 ptlogin + OAuth + QQConnectLogin；网易复用已安装 musicdl 的 weapi
加密工具。会话不会写数据库，服务重启后需要重新生成二维码。
"""
from __future__ import annotations

import base64
import io
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import qrcode
import qrcode.image.svg
import requests
from fastapi import HTTPException
from musicdl.modules.utils.neteaseutils import WeapiCryptoUtils
from musicdl.modules.utils.qqutils import QQMusicClientUtils

from . import webconfig

_TTL = 180
_CAPACITY = 16
_SESSIONS: dict[str, "LoginSession"] = {}
_LOCK = threading.Lock()
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


@dataclass
class LoginSession:
    source: str
    key: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    expires_at: float = field(default_factory=lambda: time.time() + _TTL)
    status: str = "waiting"
    message: str = "等待扫码"
    platform_key: str = ""
    last_poll: float = 0
    pending_cookies: dict | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    canceled: threading.Event = field(default_factory=threading.Event)
    http: requests.Session = field(default_factory=requests.Session)

    def snapshot(self) -> dict:
        return {"key": self.key, "source": self.source, "status": self.status,
                "message": self.message, "expires_at": int(self.expires_at),
                "remaining_s": max(0, int(self.expires_at - time.time())),
                "saved": self.status == "success"}


def _check_source(source: str) -> None:
    if source not in {"qq", "netease"}:
        raise HTTPException(400, "扫码登录仅支持 qq / netease")


def _lookup(source: str, key: str) -> LoginSession:
    _check_source(source)
    with _LOCK:
        job = _SESSIONS.get(key)
    if not job or job.source != source:
        raise HTTPException(404, "登录会话不存在，请重新生成二维码")
    return job


def create(source: str) -> dict:
    _check_source(source)
    job = LoginSession(source)
    with _LOCK:
        for key, old in list(_SESSIONS.items()):
            if old.expires_at < time.time() and old.lock.acquire(blocking=False):
                try:
                    old.http.close()
                    del _SESSIONS[key]
                finally:
                    old.lock.release()
        if len(_SESSIONS) >= _CAPACITY:
            raise HTTPException(429, "登录会话过多，请稍后再试")
        _SESSIONS[job.key] = job
    try:
        with job.lock:
            job.http.headers.update({"User-Agent": _UA})
            if source == "qq":
                job.http.headers["Referer"] = "https://xui.ptlogin2.qq.com/"
                r = job.http.get("https://ssl.ptlogin2.qq.com/ptqrshow", params={
                    "appid": 716027609, "e": 2, "l": "M", "s": 3, "d": 72,
                    "v": 4, "daid": 383, "pt_3rd_aid": 100497308,
                    "t": secrets.randbelow(1000000) / 1000000,
                }, timeout=15)
                r.raise_for_status()
                job.platform_key = r.cookies.get("qrsig", "")
                if not job.platform_key or not r.content.startswith(b"\x89PNG"):
                    raise ValueError("二维码响应无效")
                image = "data:image/png;base64," + base64.b64encode(r.content).decode()
            else:
                job.http.headers.update({"Referer": "https://music.163.com/"})
                data = _netease_request(job, "unikey", {"type": 3})
                job.platform_key = (data.get("data") or {}).get("unikey") or data.get("unikey", "")
                if not job.platform_key or data.get("code") != 200:
                    raise ValueError("二维码响应无效")
                qr = qrcode.make("https://music.163.com/login?codekey=" + job.platform_key,
                                 image_factory=qrcode.image.svg.SvgPathImage)
                buf = io.BytesIO()
                qr.save(buf)
                image = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
            return {**job.snapshot(), "image_url": image}
    except Exception:
        with _LOCK:
            _SESSIONS.pop(job.key, None)
        job.http.close()
        raise HTTPException(502, "获取登录二维码失败，请检查网络后重试") from None


def poll(source: str, key: str) -> dict:
    job = _lookup(source, key)
    with job.lock:
        if job.canceled.is_set() and job.status != "success":
            job.status, job.message = "expired", "登录已取消"
        if job.status in {"success", "expired", "failed"}:
            return job.snapshot()
        if time.time() >= job.expires_at:
            job.status, job.message = "expired", "二维码已过期，请刷新"
            job.http.close()
            return job.snapshot()
        if time.monotonic() - job.last_poll < 2:
            return job.snapshot()
        job.last_poll = time.monotonic()
        try:
            if job.pending_cookies is None:
                if source == "qq":
                    _poll_qq(job)
                else:
                    _poll_netease(job)
        except requests.RequestException:
            job.message = "平台连接暂时失败，正在重试"
        except (ValueError, KeyError, TypeError):
            job.status, job.message = "failed", "平台登录响应异常，请重新扫码"
        if job.pending_cookies is not None:
            if job.canceled.is_set() or time.time() >= job.expires_at:
                job.status, job.message = "expired", "登录已取消或过期，请重新扫码"
                job.pending_cookies = None
            else:
                source_name = "QQMusicClient" if source == "qq" else "NeteaseMusicClient"
                try:
                    saved = webconfig.save_login_cookies(
                        source_name, job.pending_cookies,
                        lambda: not job.canceled.is_set() and time.time() < job.expires_at,
                    )
                except HTTPException:
                    job.status, job.message = "scanned", "已确认登录，但凭证保存失败；检查配置写入权限后将自动重试"
                else:
                    if saved:
                        job.status, job.message = "success", "登录成功，凭证已保存并生效"
                    else:
                        job.status, job.message = "expired", "登录已取消或过期，请重新扫码"
                    job.pending_cookies = None
                    job.http.close()
        return job.snapshot()


def cancel(source: str, key: str) -> dict:
    job = _lookup(source, key)
    # 立即通知在途请求，返回后不再保存尚未开始写入的凭证。
    job.canceled.set()
    with job.lock:
        if job.status != "success":
            job.status, job.message = "expired", "登录已取消"
            job.pending_cookies = None
        job.http.close()
        return job.snapshot()


def _netease_request(job: LoginSession, action: str, payload: dict) -> dict:
    r = job.http.post("https://music.163.com/weapi/login/qrcode/" + action,
                      data=WeapiCryptoUtils.encryptparams({**payload, "csrf_token": ""}), timeout=15)
    r.raise_for_status()
    return r.json()


def _poll_netease(job: LoginSession) -> None:
    data = _netease_request(job, "client/login", {"key": job.platform_key, "type": 3})
    code = data.get("code")
    if code == 800:
        job.status, job.message = "expired", "二维码已过期，请刷新"
    elif code == 801:
        job.status, job.message = "waiting", "请使用网易云音乐 App 扫码"
    elif code == 802:
        job.status, job.message = "scanned", "已扫码，请在手机上确认登录"
    elif code == 803:
        cookies = job.http.cookies.get_dict()
        if not cookies.get("MUSIC_U"):
            raise ValueError("缺少登录凭证")
        job.pending_cookies = cookies
    else:
        job.status, job.message = "failed", f"网易云拒绝登录（状态码 {code}），请重新扫码"


def _poll_qq(job: LoginSession) -> None:
    r = job.http.get("https://ssl.ptlogin2.qq.com/ptqrlogin", params={
        "aid": 716027609, "daid": 383, "pt_3rd_aid": 100497308,
        "ptqrtoken": QQMusicClientUtils.hash33(job.platform_key),
        "u1": "https://graph.qq.com/oauth2.0/login_jump", "ptredirect": 0,
        "h": 1, "t": 1, "g": 1, "from_ui": 1, "ptlang": 2052,
        "action": f"0-0-{int(time.time() * 1000)}", "js_ver": 20102616,
        "js_type": 1, "pt_uistyle": 40, "has_onekey": 1,
    }, cookies={"qrsig": job.platform_key}, timeout=15)
    r.raise_for_status()
    callback = re.fullmatch(r"\s*ptuiCB\((.*)\);?\s*", r.text, re.S)
    if not callback:
        raise ValueError("无效平台响应")
    # 只解析 callback 字符串参数，绝不执行 JavaScript。
    args = re.findall(r"'((?:[^'\\]|\\.)*)'", callback.group(1))
    code = args[0] if args else ""
    if code in {"66", "67"}:
        job.status = "waiting" if code == "66" else "scanned"
        job.message = "请使用 QQ App 扫码" if code == "66" else "已扫码，请在手机上确认登录"
    elif code == "65":
        job.status, job.message = "expired", "二维码已过期，请刷新"
    elif code == "0" and len(args) >= 3:
        params = parse_qs(urlparse(args[2].replace("\\/", "/")).query)
        _authorize_qq(job, params["uin"][0], params["ptsigx"][0])
    else:
        job.status, job.message = "failed", "QQ 登录被取消或拒绝，请重新扫码"


def _authorize_qq(job: LoginSession, uin: str, sig: str) -> None:
    # 固定地址请求，回调 URL 仅提取参数；不追随平台或用户提供的重定向。
    checked = job.http.get("https://ssl.ptlogin2.graph.qq.com/check_sig", params={
        "uin": uin, "ptsigx": sig, "service": "ptqrlogin", "pttype": 1,
        "nodirect": 0, "s_url": "https://graph.qq.com/oauth2.0/login_jump",
        "ptlang": 2052, "ptredirect": 100, "aid": 716027609, "daid": 383,
        "pt_3rd_aid": 100497308, "j_later": 0, "low_login_hour": 0,
        "regmaster": 0, "pt_login_type": 3, "pt_aid": 0, "pt_aaid": 16, "pt_light": 0,
    }, allow_redirects=False, timeout=15)
    checked.raise_for_status()
    skey = checked.cookies.get("p_skey", "")
    if not skey:
        raise ValueError("登录凭证缺失")
    authorized = job.http.post("https://graph.qq.com/oauth2.0/authorize", data={
        "client_id": 100497308, "response_type": "code",
        "redirect_uri": "https://y.qq.com/portal/wx_redirect.html?login_type=1&surl=https://y.qq.com/",
        "scope": "get_user_info,get_app_friends", "state": "state", "switch": "",
        "from_ptlogin": 1, "src": 1, "update_auth": 1, "openapi": "1010_1030",
        "g_tk": QQMusicClientUtils.hash33(skey, 5381),
        "auth_time": int(time.time() * 1000), "ui": secrets.token_hex(16),
    }, allow_redirects=False, timeout=15)
    authorized.raise_for_status()
    code = parse_qs(urlparse(authorized.headers.get("Location", "")).query).get("code", [None])[0]
    if not code:
        raise ValueError("授权失败")
    r = job.http.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json={
        "comm": {"ct": 24, "cv": 0, "format": "json", "tmeLoginType": 2},
        "login": {"module": "QQConnectLogin.LoginServer", "method": "QQLogin", "param": {"code": code}},
    }, timeout=15)
    r.raise_for_status()
    login = r.json().get("login") or {}
    data = login.get("data") or {}
    if login.get("code") != 0 or not data.get("musickey") or not data.get("musicid"):
        business_code = login.get("code")
        labels = {20279: "登录设备数达到上限", 20277: "账号登录受限", 104604: "操作过于频繁"}
        job.status, job.message = "failed", labels.get(business_code, "QQ 授权失败") + f"（状态码 {business_code}）"
        return
    now = int(time.time())
    job.pending_cookies = {
        "uin": str(data["musicid"]), "qqmusic_uin": str(data["musicid"]),
        "musicid": str(data["musicid"]), "qqmusic_key": data["musickey"],
        "qm_keyst": data["musickey"], "tmeLoginType": "2",
        "psrf_qqopenid": data.get("openid", ""), "psrf_qqunionid": data.get("unionid", ""),
        "psrf_qqaccess_token": data.get("access_token", ""),
        "psrf_qqrefresh_token": data.get("refresh_token", ""),
        "psrf_access_token_expiresAt": str(data.get("expired_at") or now + 5184000),
        "psrf_musickey_createtime": str(data.get("musickeyCreateTime") or now),
        "refresh_key": data.get("refresh_key", ""),
    }
