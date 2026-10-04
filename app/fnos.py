"""飞牛音乐歌单同步：纯 API 客户端（username/password 登录 + token 持久化 + 惰性重登）。

实测依据（docs/superpowers/specs/2026-10-04-fnos-playlist-design.md，2026-10-04 本机实证）：
- API 基座 {base_url}/music/api/v1/...，响应信封 {code, msg, data}，code=0 成功；
  凭证失效 code=99999（重新登录并重试一次，仍失败抛 FnosAuthError）
- 登录 POST /user/password-login：password 为明文 sha256 hex，带 deviceId（首登生成
  uuid4 hex 后持久复用）→ data.userToken；后续请求带 Cookie: music-token=<userToken>
- 服务端不校验 authx 签名头（探测矩阵 6 项全过 + 搜索端点复验），本模块不实现
- 状态文件 fnos_music_state.json 与 db_path 同目录，原子写（tmp+replace）；
  配置 username 变更时旧状态自动作废重登
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_API = "/music/api/v1"
_TIMEOUT = httpx.Timeout(15.0)
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"}
_INVALID_TOKEN = 99999


class FnosError(Exception):
    """飞牛音乐客户端错误基类。"""


class FnosNotConfiguredError(FnosError):
    """未配置 fnos_music 段。"""


class FnosAuthError(FnosError):
    """登录失败或重登后仍失效（检查 fnos_music 账号密码）。"""


class FnosApiError(FnosError):
    """飞牛接口返回业务错误（code != 0 且非 99999）。"""

    def __init__(self, code: int | None, msg: str = ""):
        super().__init__(f"飞牛接口错误 code={code} msg={msg}")
        self.code = code
        self.msg = msg


class FnosPlaylistNotFound(FnosError):
    """按名定位歌单失败（严格语义端点映射 404）。"""


# ---- 状态文件（与 db_path 同目录，容器内已持久化） ----

def _state_path() -> Path:
    return Path(settings.db_path).parent / "fnos_music_state.json"


def _load_state() -> dict | None:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


class FnosClient:
    def __init__(self, base_url: str, username: str, password: str,
                 path_map: dict[str, str] | None = None, scan_wait_s: int = 120,
                 verify_tls: bool = False):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.path_map = dict(path_map or {})
        self.scan_wait_s = scan_wait_s
        self.verify_tls = verify_tls
        self._token: str | None = None
        self._device_id: str | None = None

    # —— 认证状态机 ——

    def ensure_token(self) -> str:
        if self._token:
            return self._token
        state = _load_state() or {}
        if state.get("userToken") and state.get("username") == self.username:
            self._token = state["userToken"]
            self._device_id = state.get("deviceId")
            return self._token
        self.login()
        return self._token

    def login(self) -> None:
        device_id = self._device_id or (_load_state() or {}).get("deviceId") or uuid.uuid4().hex
        r = httpx.post(self.base_url + _API + "/user/password-login",
                       json={"username": self.username,
                             "password": hashlib.sha256(self.password.encode("utf-8")).hexdigest(),
                             "deviceId": device_id},
                       headers=_UA, verify=self.verify_tls, timeout=_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        token = (body.get("data") or {}).get("userToken")
        if body.get("code") != 0 or not token:
            raise FnosAuthError(f"飞牛音乐登录失败（code={body.get('code')} msg={body.get('msg')}），"
                                f"请检查 fnos_music 账号密码")
        self._token = token
        self._device_id = device_id
        _save_state({"userToken": token, "deviceId": device_id,
                     "username": self.username, "login_at": int(time.time())})

    def request(self, method: str, api_path: str, **kw) -> dict:
        """带 music-token 调 API，返回信封 data；code==99999 重登后重试一次。"""
        self.ensure_token()
        body = self._raw(method, api_path, **kw)
        if body.get("code") == _INVALID_TOKEN:
            logger.info("飞牛 music-token 失效，重新登录后重试")
            self._token = None
            self.login()
            body = self._raw(method, api_path, **kw)
            if body.get("code") == _INVALID_TOKEN:
                raise FnosAuthError("重登后仍返回 INVALID TOKEN，请检查 fnos_music 账号密码")
        if body.get("code") != 0:
            raise FnosApiError(body.get("code"), body.get("msg") or "")
        return body.get("data")

    def _raw(self, method: str, api_path: str, **kw) -> dict:
        headers = {**_UA, "Cookie": f"music-token={self._token or ''}"}
        r = httpx.request(method, self.base_url + _API + api_path, headers=headers,
                          verify=self.verify_tls, timeout=_TIMEOUT, **kw)
        r.raise_for_status()
        return r.json()

    # —— 歌单原语 ——

    def list_playlists(self) -> list[dict]:
        data = self.request("GET", "/playlist/list")
        return [{"guid": p.get("guid"), "name": p.get("name"),
                 "track_count": p.get("trackCount")}
                for p in (data or {}).get("list") or []]

    def create_playlist(self, name: str) -> str:
        data = self.request("POST", "/playlist/create", json={"name": name})
        guid = (data or {}).get("guid")
        if not guid:
            raise FnosApiError(None, f"创建歌单未返回 guid: {data}")
        return guid

    def playlist_tracks(self, playlist_guid: str) -> list[dict]:
        """歌单内全部曲目（完整对象，分页拉全，追加前去重依据）。"""
        out: list[dict] = []
        page, size = 1, 300
        while True:
            data = self.request("GET", "/track/playlist-detail/list",
                                params={"playlistGUID": playlist_guid, "page": page, "size": size})
            items = (data or {}).get("list") or []
            out.extend(items)
            total = (data or {}).get("total") or 0
            if not items or len(out) >= total:
                return out
            page += 1

    def add_tracks(self, playlist_guid: str, track_guids: list[str]) -> None:
        if not track_guids:
            return
        self.request("POST", "/playlist/add-track",
                     json={"guid": playlist_guid, "trackGUIDs": list(track_guids)})

    # —— guid 解析 ——

    def to_host_path(self, container_path: str) -> str | None:
        """容器路径 → 宿主路径（path_map 最长前缀匹配，按目录边界）；无匹配返回 None。"""
        for prefix in sorted(self.path_map, key=len, reverse=True):
            p = prefix.rstrip("/")
            if container_path == p or container_path.startswith(p + "/"):
                return self.path_map[prefix].rstrip("/") + container_path[len(p):]
        return None

    def resolve_guids(self, container_paths: list[str]) -> dict[str, str | None]:
        """容器路径集合 → 飞牛曲目 guid（按宿主路径精确匹配 track/list）。

        path_map 无前缀匹配的路径直接记 None 不等待；未命中的在 scan_wait_s 内轮询
        重扫（等飞牛 watcher 收编新文件），超时仍缺记 None。返回键为入参容器路径。
        """
        resolved: dict[str, str | None] = {}
        pending: dict[str, str] = {}  # host_path → container_path
        for cp in dict.fromkeys(container_paths):  # 去重保序
            hp = self.to_host_path(cp)
            resolved[cp] = None
            if hp is not None:
                pending[hp] = cp
        if not pending:
            return resolved
        deadline = time.monotonic() + self.scan_wait_s
        while True:
            for hp, guid in self._scan_track_list(set(pending)).items():
                if guid:
                    resolved[pending.pop(hp)] = guid
            if not pending or time.monotonic() >= deadline:
                return resolved
            time.sleep(min(10.0, max(1.0, deadline - time.monotonic())))

    def _scan_track_list(self, targets: set[str]) -> dict[str, str | None]:
        """track/list 按 createdAt 倒序分页扫描，命中 targets 中的宿主路径即记 guid。"""
        found: dict[str, str | None] = {}
        page, size = 1, 200
        while targets - set(found):
            data = self.request("GET", "/track/list",
                                params={"page": page, "size": size, "sort": "createdAt,desc"})
            items = (data or {}).get("list") or []
            total = (data or {}).get("total") or 0
            for t in items:
                hp = (t.get("audioSpec") or {}).get("path") or ""
                if hp in targets and hp not in found:
                    found[hp] = t.get("guid")
            if not items or page * size >= total:
                break
            page += 1
        return found


# ---- 单例门面（端点与下载钩子共用） ----

_CLIENT: FnosClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> FnosClient:
    """惰性单例：按 settings.fnos_music 构建；未配置抛 FnosNotConfiguredError。"""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        cfg = settings.fnos_music
        if not cfg:
            raise FnosNotConfiguredError("未配置 fnos_music（config.yaml），飞牛歌单功能不可用")
        _CLIENT = FnosClient(base_url=cfg.base_url, username=cfg.username,
                             password=cfg.password, path_map=cfg.path_map,
                             scan_wait_s=cfg.scan_wait_s, verify_tls=cfg.verify_tls)
        return _CLIENT


def reset_client() -> None:
    """测试/配置变更用：丢弃单例，下次 get_client 重建。"""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None
