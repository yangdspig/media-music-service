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


# ---- 字段裁剪（搜索/详情出参，避免把飞牛完整对象塞给调用方） ----

def _trim_track(t: dict) -> dict:
    return {"guid": t.get("guid"), "title": t.get("title"),
            "artists": [a.get("name") for a in (t.get("artists") or []) if a.get("name")],
            "album": (t.get("album") or {}).get("name"),
            "duration": t.get("duration"),
            "path": (t.get("audioSpec") or {}).get("path")}


def _trim_album(a: dict) -> dict:
    return {"guid": a.get("guid"), "name": a.get("name"),
            "artists": [x.get("name") for x in (a.get("artists") or []) if x.get("name")],
            "track_count": a.get("trackCount")}


def _trim_artist(a: dict) -> dict:
    return {"guid": a.get("guid"), "name": a.get("name"),
            "track_count": a.get("trackCount"), "album_count": a.get("albumCount")}


def _trim_playlist(p: dict) -> dict:
    return {"guid": p.get("guid"), "name": p.get("name"),
            "track_count": p.get("trackCount")}


def _trim_group(group: dict | None, trim) -> dict:
    group = group or {}
    return {"total": group.get("total") or 0,
            "items": [trim(x) for x in (group.get("items") or [])]}


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

    def list_playlists(self, *, include_counts: bool = False) -> list[dict]:
        data = self.request("GET", "/playlist/list")
        playlists = [_trim_playlist(p) for p in (data or {}).get("list") or []]
        if include_counts:
            # 飞牛部分版本的目录不返回 trackCount；不能用目录页长或默认值代替。
            for playlist in playlists:
                playlist["track_count"] = None
                if not playlist["guid"]:
                    continue
                try:
                    playlist["track_count"] = self.playlist_track_count(playlist["guid"])
                except Exception:
                    logger.warning("读取飞牛歌单曲目数量失败 guid=%s", playlist["guid"])
        return playlists

    @staticmethod
    def _total(data: dict) -> int | None:
        total = data.get("total")
        if isinstance(total, bool) or total is None:
            return None
        try:
            count = int(total)
        except (TypeError, ValueError, OverflowError):
            return None
        return count if count >= 0 and str(total) == str(count) else None

    def playlist_track_count(self, playlist_guid: str) -> int:
        """只取一条曲目，使用详情信封 total；缺字段时再分页核对。"""
        data = self.request("GET", "/track/playlist-detail/list",
                            params={"playlistGUID": playlist_guid, "page": 1, "size": 1}) or {}
        total = self._total(data)
        if total is not None and total >= len(data.get("list") or []):
            return total
        return len(self.playlist_tracks(playlist_guid))

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
        previous_items = None
        while True:
            data = self.request("GET", "/track/playlist-detail/list",
                                params={"playlistGUID": playlist_guid, "page": page, "size": size})
            items = (data or {}).get("list") or []
            if items and items == previous_items:
                raise FnosApiError(None, "歌单曲目分页未推进，无法确认完整数量")
            previous_items = items
            out.extend(items)
            total = self._total(data or {})
            if not items or (total is not None and len(out) >= total):
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

    # —— 搜索 ——

    def search_suggest(self, q: str) -> dict:
        """模糊搜索 suggest：track/album/artist/playlist 四组各 top-5（字段裁剪）。"""
        data = self.request("GET", "/search/suggest", params={"q": q}) or {}
        return {"track": _trim_group(data.get("track"), _trim_track),
                "album": _trim_group(data.get("album"), _trim_album),
                "artist": _trim_group(data.get("artist"), _trim_artist),
                "playlist": _trim_group(data.get("playlist"), _trim_playlist)}

    def search_tracks(self, q: str, limit: int = 50) -> dict:
        """曲目全量搜索（fnos 侧无分页一次返全量）：字段裁剪 + limit 截断（1..200）。"""
        limit = min(max(int(limit or 50), 1), 200)
        data = self.request("GET", "/search/track", params={"q": q}) or {}
        items = [_trim_track(t) for t in (data.get("list") or [])[:limit]]
        return {"total": data.get("total") or 0, "returned": len(items), "items": items}

    # —— 编排 ——

    def _find_playlist(self, name: str) -> dict | None:
        return next((p for p in self.list_playlists() if p.get("name") == name), None)

    def _dedupe_add(self, playlist_guid: str, guids: list[str]) -> tuple[int, int]:
        """按歌单现有曲目去重后追加，返回 (added, already)。"""
        existing = {t.get("guid") for t in self.playlist_tracks(playlist_guid)}
        unique = list(dict.fromkeys(guids))
        to_add = [g for g in unique if g not in existing]
        if to_add:
            self.add_tracks(playlist_guid, to_add)
        return len(to_add), len(unique) - len(to_add)

    def sync_playlist(self, name: str, container_paths: list[str]) -> dict:
        """ensure 语义：歌单不存在则建；容器路径解析 guid 后去重追加（幂等）。"""
        resolved = self.resolve_guids(container_paths)
        guids = [g for g in resolved.values() if g]
        unresolved = [p for p, g in resolved.items() if not g]
        pl = self._find_playlist(name)
        pguid = pl["guid"] if pl else self.create_playlist(name)
        added, already = self._dedupe_add(pguid, guids)
        return {"status": "partial" if unresolved else "ok",
                "playlist_guid": pguid, "playlist_name": name,
                "added": added, "already": already,
                "unresolved": unresolved, "error": None}

    def append_tracks(self, name: str, container_paths: list[str] | None = None,
                      guids: list[str] | None = None) -> dict:
        """严格语义：歌单必须已存在（否则 FnosPlaylistNotFound，防打错字静默建新单）；
        guids 免路径解析直达。"""
        pl = self._find_playlist(name)
        if not pl:
            raise FnosPlaylistNotFound(f"飞牛歌单不存在: {name}")
        resolved = self.resolve_guids(container_paths or [])
        unresolved = [p for p, g in resolved.items() if not g]
        all_guids = [g for g in resolved.values() if g] + list(guids or [])
        added, already = self._dedupe_add(pl["guid"], all_guids)
        return {"status": "partial" if unresolved else "ok",
                "playlist_guid": pl["guid"], "playlist_name": name,
                "added": added, "already": already,
                "unresolved": unresolved, "error": None}

    def playlist_detail(self, name: str) -> dict:
        pl = self._find_playlist(name)
        if not pl:
            raise FnosPlaylistNotFound(f"飞牛歌单不存在: {name}")
        tracks = [_trim_track(t) for t in self.playlist_tracks(pl["guid"])]
        return {"playlist_guid": pl["guid"], "playlist_name": name,
                "count": len(tracks), "tracks": tracks}


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


# ---- 模块门面（端点与下载钩子调用；测试 monkeypatch 入口） ----

def list_playlists() -> list[dict]:
    return get_client().list_playlists(include_counts=True)


def playlist_detail(name: str) -> dict:
    return get_client().playlist_detail(name)


def sync_playlist(name: str, container_paths: list[str]) -> dict:
    return get_client().sync_playlist(name, container_paths)


def append_tracks(name: str, container_paths: list[str] | None = None,
                  guids: list[str] | None = None) -> dict:
    return get_client().append_tracks(name, container_paths, guids)


def search_suggest(q: str) -> dict:
    return get_client().search_suggest(q)


def search_tracks(q: str, limit: int = 50) -> dict:
    return get_client().search_tracks(q, limit)
