# 飞牛音乐歌单同步实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 下载入库的歌曲自动/手动同步进飞牛音乐歌单：`submit_download` 加 `playlist` 参数 + 六个原子端点（歌单列/详情/建补/严格追加 + 模糊搜索×2）+ MCP 七处。

**Architecture:** 新增 `app/fnos.py` 纯 API 客户端（password-login → userToken → music-token cookie，99999 惰性重登，token 原子写持久化）；guid 由 `track/list` 的 `audioSpec.path`（宿主路径）按 config `path_map` 精确解析；下载 `_run` 钩子在自动归档后失败隔离地调 `sync_playlist`；REST/MCP 薄层透传。

**Tech Stack:** Python 3.13 / FastAPI / httpx / pydantic / pytest（monkeypatch httpx 层，离线）/ fastmcp 2.14.1

**规格：** `docs/superpowers/specs/2026-10-04-fnos-playlist-design.md`（已定稿，接口契约均 2026-10-04 本机实证）

## Global Constraints

- 测试命令必须 `.venv/bin/python -m pytest`（console script pytest 无法 import app 包）；当前基线 **153 passed**，每个 Task 结束跑全量回归不许变红
- 全程 TDD：先写失败测试 → 确认失败 → 最小实现 → 确认通过 → commit
- **不挂载不读取 music.db；不实现 authx 签名头**（规格非目标，2026-10-04 实测服务端不校验）
- 飞牛凭证只进本地/部署侧 config，**严禁提交 git**；config.yaml 只提交注释样例
- password 仅以 sha256 hex 上送；token 持久化在 db_path 同目录 `fnos_music_state.json`，原子写（tmp+replace），不回写 config.yaml
- 本机 8765/8766 是生产实例，不可触碰；本地 E2E 用 127.0.0.1:8875
- 中文 conventional commits（`feat:` / `test:` / `docs:` / `refactor:`）
- 模块 docstring/注释用中文，风格对齐 `app/charts.py`、`app/qqauth.py`

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/fnos.py` | 新建 | 飞牛客户端全部逻辑：认证状态机 / 歌单原语 / guid 解析 / 编排 / 搜索 / 模块门面 + 单例 |
| `tests/test_fnos.py` | 新建 | fnos 全部单测 + REST 端点集成测试（httpx 层/编排层 mock，离线） |
| `app/config.py` | 修改 | `FnosMusicConfig` + `Settings.fnos_music`（None 则功能禁用） |
| `app/schemas.py` | 修改 | `DownloadRequest.playlist`、`DownloadTask.playlist/playlist_result`、`FnosPlaylistRequest`、`FnosPlaylistAppendRequest` |
| `app/download.py` | 修改 | `submit(playlist=)` + `_run` 归档后歌单同步钩子 `_sync_fnos_playlist` |
| `app/archive.py` | 修改 | `archived_container_paths(res)` 提取入库曲目容器路径 |
| `app/main.py` | 修改 | `api_submit` playlist 校验 + 六个 fnos 端点 + `_fnos_http` + `_fnos_task_paths` |
| `mcp_adapter.py` | 修改 | `submit_download` 加 `playlist?` + 六个新工具 |
| `config.yaml` | 修改 | `fnos_music` 注释样例（不含真实凭证） |
| `README.md` / `docs/API.md` / `docs/MCP.md` / `ROADMAP.md` / 规格文档 | 修改 | 文档同步（Task 7） |

---

### Task 1: FnosMusicConfig 配置 + fnos 客户端基座（认证状态机 + 状态持久化 + 单例门面）

**Files:**
- Create: `app/fnos.py`
- Modify: `app/config.py:56-82`（AuthRefreshConfig 后加 FnosMusicConfig，Settings 加字段）
- Modify: `config.yaml`（末尾加注释样例）
- Test: `tests/test_fnos.py`（新建）

**Interfaces:**
- Consumes: `app.config.settings`（`db_path` 定位状态文件目录）
- Produces（后续任务依赖的确切名字）:
  - 异常：`FnosError` / `FnosNotConfiguredError` / `FnosAuthError` / `FnosApiError(code, msg)` / `FnosPlaylistNotFound`
  - `FnosClient(base_url, username, password, path_map=None, scan_wait_s=120, verify_tls=False)`
  - 方法：`ensure_token() -> str`、`login() -> None`、`request(method, api_path, **kw) -> dict`（返回信封的 `data`）
  - 模块函数：`get_client() -> FnosClient`、`reset_client() -> None`
  - `app.config.FnosMusicConfig`（字段：`base_url, username, password, verify_tls=False, scan_wait_s=120, path_map={}`）；`Settings.fnos_music: FnosMusicConfig | None = None`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fnos.py`：

```python
"""飞牛音乐歌单同步单元测试：httpx 层 mock，离线运行。"""
import hashlib
import json

import httpx
import pytest

from app import fnos


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


def _client(tmp_path, monkeypatch, scan_wait_s=0):
    """构造测试客户端：状态文件指向 tmp_path，path_map 与测试固件一致。"""
    monkeypatch.setattr(fnos, "_state_path", lambda: tmp_path / "fnos_music_state.json")
    return fnos.FnosClient("https://fnos.test:5667", "user", "pw",
                           path_map={"/singles": "/vol1/1000/Media/Singles",
                                     "/library": "/vol1/1000/Media/Music"},
                           scan_wait_s=scan_wait_s)


def _state(tmp_path):
    """写入一份有效状态文件（跳过登录）。"""
    (tmp_path / "fnos_music_state.json").write_text(json.dumps(
        {"userToken": "t", "deviceId": "d", "username": "user"}), encoding="utf-8")


def _router(routes):
    """按 URL 后缀分发 FakeResp；payload 为 callable 时以 (method, url, kw) 调用。"""
    def _fake(method, url, **kw):
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                if callable(payload):
                    return payload(method, url, kw)
                return FakeResp(payload)
        raise AssertionError(f"未 mock 的请求: {url}")
    return _fake


def _track(guid, path):
    return {"guid": guid, "title": "T", "audioSpec": {"path": path}}


# ---- 认证状态机 ----

def test_login_posts_sha256_and_persists_state(tmp_path, monkeypatch):
    captured = {}

    def _fake_post(url, **kw):
        captured["url"] = url
        captured["json"] = kw.get("json")
        return FakeResp({"code": 0, "data": {"userToken": "tok123"}})

    monkeypatch.setattr(httpx, "post", _fake_post)
    c = _client(tmp_path, monkeypatch)
    c.login()
    assert captured["url"] == "https://fnos.test:5667/music/api/v1/user/password-login"
    assert captured["json"]["username"] == "user"
    assert captured["json"]["password"] == hashlib.sha256(b"pw").hexdigest()
    assert len(captured["json"]["deviceId"]) == 32
    state = json.loads((tmp_path / "fnos_music_state.json").read_text(encoding="utf-8"))
    assert state["userToken"] == "tok123"
    assert state["username"] == "user"
    assert state["deviceId"] == captured["json"]["deviceId"]


def test_login_failure_raises_auth_error(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **kw: FakeResp({"code": 10001, "msg": "密码错误"}))
    with pytest.raises(fnos.FnosAuthError):
        _client(tmp_path, monkeypatch).login()


def test_ensure_token_prefers_state_file(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("不应触发登录"))
    c = _client(tmp_path, monkeypatch)
    assert c.ensure_token() == "t"


def test_ensure_token_relogs_on_username_mismatch(tmp_path, monkeypatch):
    (tmp_path / "fnos_music_state.json").write_text(json.dumps(
        {"userToken": "old", "deviceId": "d", "username": "other"}), encoding="utf-8")
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **kw: FakeResp({"code": 0, "data": {"userToken": "new"}}))
    c = _client(tmp_path, monkeypatch)
    assert c.ensure_token() == "new"


def test_request_sends_music_token_cookie(tmp_path, monkeypatch):
    _state(tmp_path)
    captured = {}

    def _fake_request(method, url, **kw):
        captured["headers"] = kw.get("headers") or {}
        return FakeResp({"code": 0, "data": {"ok": True}})

    monkeypatch.setattr(httpx, "request", _fake_request)
    assert _client(tmp_path, monkeypatch).request("GET", "/playlist/list") == {"ok": True}
    assert "music-token=t" in captured["headers"].get("Cookie", "")


def test_request_relogin_once_on_99999(tmp_path, monkeypatch):
    _state(tmp_path)
    calls = {"login": 0, "api": 0}

    def _fake_post(url, **kw):
        calls["login"] += 1
        return FakeResp({"code": 0, "data": {"userToken": "fresh"}})

    def _fake_request(method, url, **kw):
        calls["api"] += 1
        return FakeResp({"code": 99999} if calls["api"] == 1 else {"code": 0, "data": {"ok": 1}})

    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr(httpx, "request", _fake_request)
    c = _client(tmp_path, monkeypatch)
    assert c.request("GET", "/playlist/list") == {"ok": 1}
    assert calls == {"login": 1, "api": 2}


def test_request_persistent_99999_raises_auth_error(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **kw: FakeResp({"code": 0, "data": {"userToken": "fresh"}}))
    monkeypatch.setattr(httpx, "request", lambda *a, **kw: FakeResp({"code": 99999}))
    with pytest.raises(fnos.FnosAuthError):
        _client(tmp_path, monkeypatch).request("GET", "/playlist/list")


def test_request_nonzero_code_raises_api_error(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request",
                        lambda *a, **kw: FakeResp({"code": 40001, "msg": "bad"}))
    with pytest.raises(fnos.FnosApiError):
        _client(tmp_path, monkeypatch).request("GET", "/playlist/list")


# ---- 单例门面 ----

def test_get_client_not_configured(monkeypatch):
    monkeypatch.setattr(fnos.settings, "fnos_music", None)
    fnos.reset_client()
    with pytest.raises(fnos.FnosNotConfiguredError):
        fnos.get_client()


def test_get_client_builds_from_settings(monkeypatch):
    from app.config import FnosMusicConfig
    monkeypatch.setattr(fnos.settings, "fnos_music", FnosMusicConfig(
        base_url="https://fnos.test:5667/", username="u", password="p",
        path_map={"/singles": "/vol1/x"}))
    fnos.reset_client()
    c = fnos.get_client()
    assert c.base_url == "https://fnos.test:5667"  # 尾部斜杠已去除
    assert c.scan_wait_s == 120 and c.verify_tls is False
    assert c.path_map == {"/singles": "/vol1/x"}
    fnos.reset_client()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -x -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.fnos'`）

- [ ] **Step 3: 实现**

`app/config.py`——在 `AuthRefreshConfig` 类后新增：

```python
class FnosMusicConfig(BaseModel):
    """飞牛音乐歌单同步：纯 API 客户端（不挂载不读取 music.db）。

    凭证为飞牛音乐应用内独立账号（非 fnOS 系统账号）；password 仅用于登录时 sha256
    后请求 password-login，不回写本文件；token 持久化在 db_path 同目录的
    fnos_music_state.json，失效自动重登。
    """
    base_url: str                       # 飞牛 nginx 入口，如 "https://192.168.254.112:5667"
    username: str                       # 飞牛音乐应用账号
    password: str                       # 飞牛音乐应用密码（内网明文，与 cookies 同级）
    verify_tls: bool = False            # 自签证书默认关校验
    scan_wait_s: int = 120              # 等飞牛扫描新入库文件的最长秒数
    path_map: dict[str, str] = {}       # 容器内库根 → 宿主路径（飞牛 audioSpec.path 前缀）
```

`Settings` 增加字段（放在 `auth_refresh` 行后）：

```python
    fnos_music: FnosMusicConfig | None = None  # 飞牛音乐歌单同步；未配置则功能整体禁用
```

新建 `app/fnos.py`：

```python
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
```

`config.yaml` 末尾追加（注释样例，不含真实凭证）：

```yaml
# 飞牛音乐歌单同步（可选）：配置后启用「下载入库 → 自动同步飞牛歌单」及歌单管理端点
# 纯 API 客户端（不挂载不读取 music.db）；凭证为飞牛音乐应用内账号（非 fnOS 系统账号）；
# password 仅用于登录获取 token（sha256 传输），token 持久化在 data/fnos_music_state.json，不回写本文件
# 注意：path_map 的键为本服务【容器内】库根，值为【飞牛宿主机】路径（飞牛 audioSpec.path 前缀）
# fnos_music:
#   base_url: "https://192.168.254.112:5667"
#   username: "音乐应用账号"
#   password: "音乐应用密码"
#   verify_tls: false
#   scan_wait_s: 120
#   path_map:
#     "/library": "/vol1/1000/Media/Music"
#     "/singles": "/vol1/1000/Media/Singles"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -q`
Expected: 10 passed

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 163 passed）

```bash
git add app/fnos.py tests/test_fnos.py app/config.py config.yaml
git commit -m "feat: 飞牛音乐客户端基座（password-login 认证状态机 + token 原子持久化 + fnos_music 配置段）"
```

---

### Task 2: 歌单原语 + guid 解析（list/create/tracks/add + to_host_path + resolve_guids）

**Files:**
- Modify: `app/fnos.py`（FnosClient 增加方法）
- Test: `tests/test_fnos.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `FnosClient.request`
- Produces:
  - `FnosClient.list_playlists() -> list[dict]`（`{"guid", "name", "track_count"}`）
  - `FnosClient.create_playlist(name) -> str`（guid）
  - `FnosClient.playlist_tracks(playlist_guid) -> list[dict]`（完整曲目对象，分页拉全）
  - `FnosClient.add_tracks(playlist_guid, track_guids) -> None`
  - `FnosClient.to_host_path(container_path) -> str | None`
  - `FnosClient.resolve_guids(container_paths) -> dict[str, str | None]`（键为入参容器路径）

- [ ] **Step 1: 写失败测试**

`tests/test_fnos.py` 追加：

```python
# ---- 歌单原语 ----

def test_list_playlists(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": [
            {"guid": "g1", "name": "最爱", "trackCount": 3},
            {"guid": "g2", "name": "榜单"}]}}}))
    out = _client(tmp_path, monkeypatch).list_playlists()
    assert out == [{"guid": "g1", "name": "最爱", "track_count": 3},
                   {"guid": "g2", "name": "榜单", "track_count": None}]


def test_create_playlist(tmp_path, monkeypatch):
    _state(tmp_path)
    captured = {}

    def _fake(method, url, **kw):
        captured.update(url=url, json=kw.get("json"))
        return FakeResp({"code": 0, "data": {"guid": "newg"}})

    monkeypatch.setattr(httpx, "request", _fake)
    assert _client(tmp_path, monkeypatch).create_playlist("我的榜单") == "newg"
    assert captured["url"].endswith("/music/api/v1/playlist/create")
    assert captured["json"] == {"name": "我的榜单"}


def test_playlist_tracks_pagination(tmp_path, monkeypatch):
    _state(tmp_path)
    pages = {1: [{"guid": f"t{i}"} for i in range(3)], 2: [{"guid": "t3"}]}

    def _fake(method, url, **kw):
        params = kw.get("params") or {}
        assert params["playlistGUID"] == "pg"
        return FakeResp({"code": 0, "data": {"list": pages[params["page"]], "total": 4}})

    monkeypatch.setattr(httpx, "request", _fake)
    out = _client(tmp_path, monkeypatch).playlist_tracks("pg")
    assert [t["guid"] for t in out] == ["t0", "t1", "t2", "t3"]


def test_add_tracks_posts_batch(tmp_path, monkeypatch):
    _state(tmp_path)
    captured = {}

    def _fake(method, url, **kw):
        captured.update(url=url, json=kw.get("json"))
        return FakeResp({"code": 0, "data": None})

    monkeypatch.setattr(httpx, "request", _fake)
    _client(tmp_path, monkeypatch).add_tracks("pg", ["a", "b"])
    assert captured["url"].endswith("/music/api/v1/playlist/add-track")
    assert captured["json"] == {"guid": "pg", "trackGUIDs": ["a", "b"]}


# ---- guid 解析 ----

def test_to_host_path_prefix_match(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.to_host_path("/singles/阿桑/叶子.flac") == "/vol1/1000/Media/Singles/阿桑/叶子.flac"
    assert c.to_host_path("/library/王菲/我也不想这样.flac") == "/vol1/1000/Media/Music/王菲/我也不想这样.flac"
    assert c.to_host_path("/downloads/x.flac") is None


def test_resolve_guids_hit_first_page(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/track/list": {"code": 0, "data": {
            "list": [_track("g1", "/vol1/1000/Media/Singles/阿桑/叶子.flac")], "total": 1}}}))
    out = _client(tmp_path, monkeypatch).resolve_guids(["/singles/阿桑/叶子.flac"])
    assert out == {"/singles/阿桑/叶子.flac": "g1"}


def test_resolve_guids_unmapped_and_missed(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/track/list": {"code": 0, "data": {"list": [], "total": 0}}}))
    # scan_wait_s=0 → 未命中不等待直接记 None
    out = _client(tmp_path, monkeypatch).resolve_guids(["/downloads/x.flac", "/singles/未扫到.flac"])
    assert out == {"/downloads/x.flac": None, "/singles/未扫到.flac": None}


def test_resolve_guids_hit_on_second_scan(tmp_path, monkeypatch):
    _state(tmp_path)
    c = _client(tmp_path, monkeypatch, scan_wait_s=120)
    scans = {"n": 0}

    def _fake(method, url, **kw):
        scans["n"] += 1
        data = ({"list": [], "total": 0} if scans["n"] == 1
                else {"list": [_track("g9", "/vol1/1000/Media/Singles/新曲.flac")], "total": 1})
        return FakeResp({"code": 0, "data": data})

    monkeypatch.setattr(httpx, "request", _fake)
    monkeypatch.setattr(fnos.time, "sleep", lambda s: None)
    out = c.resolve_guids(["/singles/新曲.flac"])
    assert out == {"/singles/新曲.flac": "g9"}
    assert scans["n"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -x -q`
Expected: FAIL（`AttributeError: 'FnosClient' object has no attribute 'list_playlists'`）

- [ ] **Step 3: 实现**

`app/fnos.py` 的 `FnosClient` 内、`request`/`_raw` 之后追加：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -q`
Expected: 18 passed

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 171 passed）

```bash
git add app/fnos.py tests/test_fnos.py
git commit -m "feat: 飞牛歌单原语与 guid 解析（playlist CRUD 原语 + path_map 最长前缀 + track/list 分页轮询）"
```

---

### Task 3: 编排层（sync_playlist / append_tracks / playlist_detail）+ 搜索（suggest / tracks）+ 模块门面

**Files:**
- Modify: `app/fnos.py`
- Test: `tests/test_fnos.py`（追加）

**Interfaces:**
- Consumes: Task 2 全部原语
- Produces（Task 4/5 依赖）:
  - `FnosClient.sync_playlist(name, container_paths) -> dict`（ensure 语义）
  - `FnosClient.append_tracks(name, container_paths=None, guids=None) -> dict`（严格语义，404 抛 `FnosPlaylistNotFound`）
  - `FnosClient.playlist_detail(name) -> dict`（`{playlist_guid, playlist_name, count, tracks}`）
  - `FnosClient.search_suggest(q) -> dict`（`{track, album, artist, playlist}` 四组 `{total, items}`）
  - `FnosClient.search_tracks(q, limit=50) -> dict`（`{total, returned, items}`）
  - 返回结构统一：`{"status": "ok"/"partial", "playlist_guid", "playlist_name", "added", "already", "unresolved", "error"}`
  - 模块门面（与客户端方法同名同签）：`list_playlists / playlist_detail / sync_playlist / append_tracks / search_suggest / search_tracks`

- [ ] **Step 1: 写失败测试**

`tests/test_fnos.py` 追加：

```python
# ---- 编排：sync_playlist（ensure 语义） ----

def test_sync_playlist_creates_and_adds(tmp_path, monkeypatch):
    _state(tmp_path)
    calls = {"add": None}

    def _add(method, url, kw):
        calls["add"] = kw.get("json")
        return FakeResp({"code": 0, "data": None})

    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": []}},
        "/playlist/create": {"code": 0, "data": {"guid": "pg1"}},
        "/track/playlist-detail/list": {"code": 0, "data": {"list": [], "total": 0}},
        "/track/list": {"code": 0, "data": {
            "list": [_track("g1", "/vol1/1000/Media/Singles/A/t.flac")], "total": 1}},
        "/playlist/add-track": _add,
    }))
    out = _client(tmp_path, monkeypatch).sync_playlist("新单", ["/singles/A/t.flac"])
    assert out == {"status": "ok", "playlist_guid": "pg1", "playlist_name": "新单",
                   "added": 1, "already": 0, "unresolved": [], "error": None}
    assert calls["add"] == {"guid": "pg1", "trackGUIDs": ["g1"]}


def test_sync_playlist_dedupes_existing(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": [{"guid": "pg1", "name": "老单"}]}},
        "/track/playlist-detail/list": {"code": 0, "data": {"list": [{"guid": "g1"}], "total": 1}},
        "/track/list": {"code": 0, "data": {"list": [
            _track("g1", "/vol1/1000/Media/Singles/A/t.flac"),
            _track("g2", "/vol1/1000/Media/Singles/A/u.flac")], "total": 2}},
        "/playlist/add-track": FakeResp({"code": 0, "data": None}),
    }))
    out = _client(tmp_path, monkeypatch).sync_playlist(
        "老单", ["/singles/A/t.flac", "/singles/A/u.flac"])
    assert out["status"] == "ok" and out["added"] == 1 and out["already"] == 1
    assert out["playlist_guid"] == "pg1"  # 已存在则不新建


def test_sync_playlist_partial_on_unresolved(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": [{"guid": "pg1", "name": "单"}]}},
        "/track/playlist-detail/list": {"code": 0, "data": {"list": [], "total": 0}},
        "/track/list": {"code": 0, "data": {"list": [], "total": 0}},
    }))
    out = _client(tmp_path, monkeypatch).sync_playlist("单", ["/singles/未扫到.flac"])
    assert out["status"] == "partial"
    assert out["unresolved"] == ["/singles/未扫到.flac"]
    assert out["added"] == 0 and out["already"] == 0


# ---- 编排：append_tracks（严格语义） ----

def test_append_tracks_strict_404(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": []}}}))
    with pytest.raises(fnos.FnosPlaylistNotFound):
        _client(tmp_path, monkeypatch).append_tracks("没有", guids=["x"])


def test_append_tracks_guids_skip_resolve(tmp_path, monkeypatch):
    _state(tmp_path)
    calls = {"add": None}

    def _add(method, url, kw):
        calls["add"] = kw.get("json")
        return FakeResp({"code": 0, "data": None})

    def _guard(method, url, kw):
        raise AssertionError("guids 直达不应触发 track/list 解析")

    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": [{"guid": "pg1", "name": "单"}]}},
        "/track/playlist-detail/list": {"code": 0, "data": {"list": [], "total": 0}},
        "/track/list": _guard,
        "/playlist/add-track": _add,
    }))
    out = _client(tmp_path, monkeypatch).append_tracks("单", guids=["gX"])
    assert out["status"] == "ok" and out["added"] == 1
    assert calls["add"] == {"guid": "pg1", "trackGUIDs": ["gX"]}


def test_playlist_detail(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({
        "/playlist/list": {"code": 0, "data": {"list": [{"guid": "pg1", "name": "单"}]}},
        "/track/playlist-detail/list": {"code": 0, "data": {"list": [
            {"guid": "g1", "title": "叶子", "artists": [{"name": "阿桑"}],
             "album": {"name": "X"}, "duration": 300000,
             "audioSpec": {"path": "/vol1/1000/Media/Singles/阿桑/叶子.flac"}}], "total": 1}},
    }))
    out = _client(tmp_path, monkeypatch).playlist_detail("单")
    assert out["playlist_guid"] == "pg1" and out["count"] == 1
    assert out["tracks"][0] == {"guid": "g1", "title": "叶子", "artists": ["阿桑"],
                                "album": "X", "duration": 300000,
                                "path": "/vol1/1000/Media/Singles/阿桑/叶子.flac"}


# ---- 搜索 ----

SUGGEST_RESP = {"code": 0, "data": {
    "track": {"total": 1, "items": [
        {"guid": "g1", "title": "我也不想这样", "artists": [{"name": "王菲"}],
         "album": {"name": "只爱陌生人"}, "duration": 240000,
         "audioSpec": {"path": "/vol1/1000/Media/Music/王菲/我也不想这样.flac"},
         "genres": [{"name": "流行"}], "isFavorite": False}]},
    "album": {"total": 1, "items": [
        {"guid": "a1", "name": "只爱陌生人", "artists": [{"name": "王菲"}], "trackCount": 12}]},
    "artist": {"total": 1, "items": [
        {"guid": "ar1", "name": "王菲", "trackCount": 100, "albumCount": 20}]},
    "playlist": {"total": 0, "items": []},
}}


def test_search_suggest_trims_groups(tmp_path, monkeypatch):
    _state(tmp_path)
    monkeypatch.setattr(httpx, "request", _router({"/search/suggest": SUGGEST_RESP}))
    out = _client(tmp_path, monkeypatch).search_suggest("王菲")
    assert out["track"]["total"] == 1
    assert out["track"]["items"][0] == {"guid": "g1", "title": "我也不想这样",
                                        "artists": ["王菲"], "album": "只爱陌生人",
                                        "duration": 240000,
                                        "path": "/vol1/1000/Media/Music/王菲/我也不想这样.flac"}
    assert out["album"]["items"][0] == {"guid": "a1", "name": "只爱陌生人",
                                        "artists": ["王菲"], "track_count": 12}
    assert out["artist"]["items"][0] == {"guid": "ar1", "name": "王菲",
                                         "track_count": 100, "album_count": 20}
    assert out["playlist"] == {"total": 0, "items": []}


def test_search_tracks_limit_truncates(tmp_path, monkeypatch):
    _state(tmp_path)
    big = {"code": 0, "data": {
        "list": [_track(f"g{i}", f"/p/{i}.flac") for i in range(60)], "total": 60}}
    monkeypatch.setattr(httpx, "request", _router({"/search/track": big}))
    out = _client(tmp_path, monkeypatch).search_tracks("x", limit=50)
    assert out["total"] == 60 and out["returned"] == 50 and len(out["items"]) == 50
    out2 = _client(tmp_path, monkeypatch).search_tracks("x", limit=500)  # 上限 200 截断
    assert out2["returned"] == 60
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -x -q`
Expected: FAIL（`AttributeError: 'FnosClient' object has no attribute 'sync_playlist'`）

- [ ] **Step 3: 实现**

`app/fnos.py`——`FnosClient` 内 `_scan_track_list` 之后追加：

```python
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
```

`app/fnos.py` 模块级（`_save_state` 之后、`FnosClient` 之前的位置即可）追加裁剪辅助：

```python
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
```

`app/fnos.py` 文件末尾（`reset_client` 之后）追加模块门面：

```python
# ---- 模块门面（端点与下载钩子调用；测试 monkeypatch 入口） ----

def list_playlists() -> list[dict]:
    return get_client().list_playlists()


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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -q`
Expected: 26 passed

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 179 passed）

```bash
git add app/fnos.py tests/test_fnos.py
git commit -m "feat: 飞牛歌单编排与模糊搜索（ensure/严格两语义 + 去重追加 + suggest/全量搜索裁剪）"
```

---

### Task 4: schemas + archived_container_paths + submit_download playlist 参数与 _run 同步钩子

**Files:**
- Modify: `app/schemas.py`（DownloadRequest/DownloadTask 加字段）
- Modify: `app/archive.py`（文件末尾加 `archived_container_paths`）
- Modify: `app/download.py`（submit 加参 + `_sync_fnos_playlist` 钩子）
- Modify: `app/main.py`（`api_submit` 校验与透传）
- Test: `tests/test_fnos.py`（追加）

**Interfaces:**
- Consumes: Task 3 的模块门面 `app.fnos.sync_playlist(name, paths) -> dict`
- Produces:
  - `DownloadRequest.playlist: str | None`；`DownloadTask.playlist / .playlist_result: dict | None`
  - `app.archive.archived_container_paths(res: ArchiveResult) -> list[str]`
  - `app.download._sync_fnos_playlist(task, archive_res) -> None`
  - Task 5 依赖：`app.schemas.FnosPlaylistRequest` / `FnosPlaylistAppendRequest`（本任务一并定义）

- [ ] **Step 1: 写失败测试**

`tests/test_fnos.py` 追加：

```python
# ---- submit_download playlist 参数与归档后同步钩子 ----

def _api_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_submit_playlist_without_library_400():
    r = _api_client().post("/api/v1/downloads", json={
        "tracks": [{"id": "s:1", "raw": {"a": 1}}], "playlist": "榜"})
    assert r.status_code == 400
    assert "library" in r.json()["detail"]


def test_submit_playlist_without_fnos_config_400(monkeypatch):
    monkeypatch.setattr("app.main.settings.fnos_music", None)
    r = _api_client().post("/api/v1/downloads", json={
        "tracks": [{"id": "s:1", "raw": {"a": 1}}], "library": "singles", "playlist": "榜"})
    assert r.status_code == 400
    assert "fnos_music" in r.json()["detail"]


def test_submit_playlist_param_passed(monkeypatch):
    from app.config import FnosMusicConfig
    monkeypatch.setattr("app.main.settings.fnos_music", FnosMusicConfig(
        base_url="https://fnos.test:5667", username="u", password="p"))
    captured = {}

    def _fake_submit(tracks, **kw):
        captured.update(kw)
        from app.schemas import DownloadTask
        return DownloadTask(task_id="t1", total=1)

    monkeypatch.setattr("app.download.submit", _fake_submit)
    r = _api_client().post("/api/v1/downloads", json={
        "tracks": [{"id": "s:1", "raw": {"a": 1}}], "library": "singles", "playlist": "榜"})
    assert r.status_code == 200
    assert captured["playlist"] == "榜"


def _hermetic_run_env(monkeypatch):
    """_run 的 DB 副作用全部 mock 掉（不写本地 data/music_service.db）。"""
    from app import download as dl
    monkeypatch.setattr(dl, "save_task", lambda t: None)
    monkeypatch.setattr("app.storage.record_file", lambda *a, **kw: None)
    return dl


def test_run_hook_syncs_playlist_after_archive(tmp_path, monkeypatch):
    dl = _hermetic_run_env(monkeypatch)
    from app.schemas import ArchiveResult, ArchiveTrackResult, DownloadTask, Track

    def _fake_download(source, song_dicts, save_dir):
        from pathlib import Path
        (Path(save_dir) / "song_abc123.flac").write_bytes(b"x")
        return 1

    monkeypatch.setattr(dl, "download_songs", _fake_download)
    fake_res = ArchiveResult(status="success", library_dir="/singles",
                             summary={"linked": 1},
                             tracks=[ArchiveTrackResult(title="T", action="linked",
                                                        target="A/T.flac")])
    monkeypatch.setattr("app.archive.archive_tracks",
                        lambda task_id, library=None: fake_res)
    captured = {}

    def _fake_sync(name, paths):
        captured.update(name=name, paths=paths)
        return {"status": "ok", "playlist_guid": "pg", "playlist_name": name,
                "added": 1, "already": 0, "unresolved": [], "error": None}

    monkeypatch.setattr("app.fnos.sync_playlist", _fake_sync)
    task = DownloadTask(task_id="t1", total=1, save_dir=str(tmp_path),
                        library="singles", playlist="榜")
    track = Track(id="s:abc123", source="s", title="T", artists=["A"],
                  raw={"identifier": "abc123"})
    dl._run(task, [track])
    assert captured == {"name": "榜", "paths": ["/singles/A/T.flac"]}
    assert task.playlist_result["status"] == "ok"
    assert "歌单同步" in task.message


def test_run_hook_failure_isolated(tmp_path, monkeypatch):
    dl = _hermetic_run_env(monkeypatch)
    from app.schemas import ArchiveResult, ArchiveTrackResult, DownloadTask, Track

    def _fake_download(source, song_dicts, save_dir):
        from pathlib import Path
        (Path(save_dir) / "song_abc123.flac").write_bytes(b"x")
        return 1

    monkeypatch.setattr(dl, "download_songs", _fake_download)
    fake_res = ArchiveResult(status="success", library_dir="/singles",
                             summary={"linked": 1},
                             tracks=[ArchiveTrackResult(title="T", action="linked",
                                                        target="A/T.flac")])
    monkeypatch.setattr("app.archive.archive_tracks",
                        lambda task_id, library=None: fake_res)

    def _raise(name, paths):
        raise fnos.FnosAuthError("重登失败")

    monkeypatch.setattr("app.fnos.sync_playlist", _raise)
    task = DownloadTask(task_id="t1", total=1, save_dir=str(tmp_path),
                        library="singles", playlist="榜")
    track = Track(id="s:abc123", source="s", title="T", artists=["A"],
                  raw={"identifier": "abc123"})
    dl._run(task, [track])
    assert task.status == "success"  # 歌单失败不影响主链路
    assert task.playlist_result["status"] == "failed"
    assert "重登失败" in task.playlist_result["error"]
    assert any("歌单同步失败" in e for e in task.errors)


def test_archived_container_paths():
    from app.archive import archived_container_paths
    from app.schemas import ArchiveResult, ArchiveTrackResult
    res = ArchiveResult(status="partial", library_dir="/singles", summary={},
                        tracks=[ArchiveTrackResult(title="a", action="linked", target="A/a.flac"),
                                ArchiveTrackResult(title="b", action="failed", target=None),
                                ArchiveTrackResult(title="c", action="skipped", target="A/c.flac")])
    assert archived_container_paths(res) == ["/singles/A/a.flac", "/singles/A/c.flac"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -x -q`
Expected: FAIL（playlist 字段/钩子等尚未实现；首个失败为 422 或 400 文案不符）

- [ ] **Step 3: 实现**

`app/schemas.py`——`DownloadRequest` 增加字段：

```python
    playlist: Optional[str] = Field(default=None, description="飞牛音乐歌单名（可选，必须搭配 library）：下载+自动归档完成后把成功入库曲目同步进该歌单（不存在则新建，已有曲目按 guid 去重）；需配置 fnos_music")
```

`DownloadTask` 增加字段（`library` 行后）：

```python
    playlist: Optional[str] = Field(default=None, description="目标飞牛歌单名（传入时下载+归档完成后自动同步）")
    playlist_result: Optional[dict[str, Any]] = Field(default=None, description="飞牛歌单同步结果（status/added/already/unresolved/error）；未同步为 None")
```

`app/schemas.py` 文件末尾新增：

```python
class FnosPlaylistRequest(BaseModel):
    """飞牛歌单建/补请求（ensure 语义）：task_id 与 paths 可单用或叠加，均缺只建空歌单。"""
    name: str = Field(description="歌单名（不存在则新建）")
    task_id: Optional[str] = Field(default=None, description="单曲下载任务 ID：取其成功入库曲目（任务须在内存中且下载时指定了 library）")
    paths: Optional[list[str]] = Field(default=None, description="容器内库文件绝对路径清单（如 /singles/阿桑/叶子.flac）")


class FnosPlaylistAppendRequest(BaseModel):
    """严格追加到既有飞牛歌单：paths/guids 至少其一；歌单不存在返回 404。"""
    paths: Optional[list[str]] = Field(default=None, description="容器内库文件绝对路径清单")
    guids: Optional[list[str]] = Field(default=None, description="飞牛曲目 guid 清单（免路径解析直达）")
```

`app/archive.py` 文件末尾新增：

```python
def archived_container_paths(res: ArchiveResult) -> list[str]:
    """从归档结果提取成功入库曲目的容器内绝对路径（供飞牛歌单同步等后置编排使用）。"""
    if not res.library_dir:
        return []
    return [str(Path(res.library_dir) / t.target) for t in res.tracks
            if t.action in ("linked", "copied", "skipped", "tag_unsupported") and t.target]
```

`app/download.py`——`submit` 签名与 task 构造：

```python
def submit(tracks: list[DownloadTrackInput], subdir: str | None = None, library: str | None = None,
           max_size_mb: float | None = None, playlist: str | None = None) -> DownloadTask:
```

```python
    task = DownloadTask(task_id=task_id, total=len(tracks), save_dir=save_dir,
                        library=library, playlist=playlist)
```

`_run` 的归档段改造（`if task.library:` 块整体替换）：

```python
    # 指定了目标库时，下载完成后自动归档（单曲一步到位）
    if task.library:
        archive_res = None
        try:
            from .archive import archive_tracks  # 晚期 import 防循环（archive 依赖 download）
            archive_res = archive_tracks(task.task_id, library=task.library)
            task.message += f"；自动归档[{task.library}] {archive_res.status} {archive_res.summary}"
            task.errors.extend(archive_res.errors)
        except Exception as e:
            task.errors.append(f"自动归档失败: {e}")
            task.message += f"；自动归档失败: {e}"
        # 指定了飞牛歌单时，归档完成后同步（失败隔离：只记 playlist_result/errors，不动主链路）
        if task.playlist:
            _sync_fnos_playlist(task, archive_res)
    save_task(task)
```

`_run` 之后新增：

```python
def _sync_fnos_playlist(task: DownloadTask, archive_res) -> None:
    """归档完成后把成功入库曲目同步进飞牛歌单；任何失败只记任务字段，不影响下载与归档。"""
    from . import fnos  # 晚期 import 防循环
    try:
        if archive_res is None:
            raise RuntimeError("归档未完成，无法确定入库曲目")
        from .archive import archived_container_paths
        paths = archived_container_paths(archive_res)
        if not paths:
            raise RuntimeError("无成功入库曲目")
        result = fnos.sync_playlist(task.playlist, paths)
    except Exception as e:
        logger.exception("飞牛歌单同步失败 task=%s playlist=%s", task.task_id, task.playlist)
        result = {"status": "failed", "playlist_guid": None, "playlist_name": task.playlist,
                  "added": 0, "already": 0, "unresolved": [], "error": str(e)}
    task.playlist_result = result
    task.message += (f"；歌单同步[{task.playlist}] {result['status']}"
                     f"（新增 {result['added']}，已存在 {result['already']}，"
                     f"未解析 {len(result['unresolved'])}）")
    if result.get("error"):
        task.errors.append(f"歌单同步失败: {result['error']}")
```

`app/main.py`——`api_submit` 替换为：

```python
@app.post("/api/v1/downloads", response_model=DownloadTask, dependencies=[Depends(auth)])
def api_submit(req: DownloadRequest) -> DownloadTask:
    if not req.tracks:
        raise HTTPException(status_code=400, detail="tracks 不能为空")
    if req.playlist and not req.library:
        raise HTTPException(status_code=400,
                            detail="playlist 必须搭配 library 使用（不入库的曲目飞牛音乐管不到）")
    if req.playlist and not settings.fnos_music:
        raise HTTPException(status_code=400,
                            detail="传了 playlist 但未配置 fnos_music（config.yaml）")
    try:
        return dl.submit(req.tracks, subdir=req.subdir, library=req.library,
                         max_size_mb=req.max_size_mb, playlist=req.playlist)
    except (ValueError, LookupError, RuntimeError) as e:  # 全部超限 / 未知库名 / 未配置默认库
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -q`
Expected: 32 passed

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 185 passed）

```bash
git add app/schemas.py app/archive.py app/download.py app/main.py tests/test_fnos.py
git commit -m "feat: submit_download 支持 playlist 参数（归档后自动同步飞牛歌单，失败隔离记 playlist_result）"
```

---

### Task 5: REST 六端点（歌单列/详情/建补/严格追加 + 搜索×2）

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_fnos.py`（追加）

**Interfaces:**
- Consumes: Task 3 模块门面（`app.fnos.*`）、Task 4 的 `FnosPlaylistRequest`/`FnosPlaylistAppendRequest`、`app.archive.archived_container_paths`
- Produces（Task 6 MCP 调用的确切路由）:
  - `GET /api/v1/fnos/playlists`
  - `GET /api/v1/fnos/playlists/{name}/tracks`
  - `POST /api/v1/fnos/playlists`（body `FnosPlaylistRequest`）
  - `POST /api/v1/fnos/playlists/{name}/tracks`（body `FnosPlaylistAppendRequest`）
  - `GET /api/v1/fnos/search?q=`
  - `GET /api/v1/fnos/search/tracks?q=&limit=`

- [ ] **Step 1: 写失败测试**

`tests/test_fnos.py` 追加：

```python
# ---- REST 端点（fnos 编排层 mock） ----

def test_api_list_playlists(monkeypatch):
    monkeypatch.setattr("app.fnos.list_playlists",
                        lambda: [{"guid": "g", "name": "单", "track_count": 1}])
    r = _api_client().get("/api/v1/fnos/playlists")
    assert r.status_code == 200 and r.json()[0]["name"] == "单"


def test_api_not_configured_400(monkeypatch):
    def _raise():
        raise fnos.FnosNotConfiguredError("未配置 fnos_music（config.yaml），飞牛歌单功能不可用")

    monkeypatch.setattr("app.fnos.list_playlists", _raise)
    r = _api_client().get("/api/v1/fnos/playlists")
    assert r.status_code == 400 and "fnos_music" in r.json()["detail"]


def test_api_fnos_failure_502(monkeypatch):
    def _raise():
        raise fnos.FnosApiError(40001, "bad")

    monkeypatch.setattr("app.fnos.list_playlists", _raise)
    r = _api_client().get("/api/v1/fnos/playlists")
    assert r.status_code == 502


def test_api_playlist_detail_404(monkeypatch):
    def _raise(name):
        raise fnos.FnosPlaylistNotFound(f"飞牛歌单不存在: {name}")

    monkeypatch.setattr("app.fnos.playlist_detail", _raise)
    r = _api_client().get("/api/v1/fnos/playlists/没有/tracks")
    assert r.status_code == 404


def test_api_playlist_detail_ok(monkeypatch):
    monkeypatch.setattr("app.fnos.playlist_detail",
                        lambda name: {"playlist_guid": "g", "playlist_name": name,
                                      "count": 0, "tracks": []})
    r = _api_client().get("/api/v1/fnos/playlists/单/tracks")
    assert r.status_code == 200 and r.json()["playlist_name"] == "单"


def test_api_sync_by_paths(monkeypatch):
    captured = {}

    def _fake(name, paths):
        captured.update(name=name, paths=paths)
        return {"status": "ok", "added": 1, "already": 0, "unresolved": []}

    monkeypatch.setattr("app.fnos.sync_playlist", _fake)
    r = _api_client().post("/api/v1/fnos/playlists",
                           json={"name": "单", "paths": ["/singles/A/t.flac"]})
    assert r.status_code == 200
    assert captured == {"name": "单", "paths": ["/singles/A/t.flac"]}


def test_api_sync_empty_creates_playlist(monkeypatch):
    monkeypatch.setattr("app.fnos.sync_playlist",
                        lambda name, paths: {"status": "ok", "added": 0} if paths == [] else None)
    r = _api_client().post("/api/v1/fnos/playlists", json={"name": "空单"})
    assert r.status_code == 200


def test_api_sync_task_not_found_400(monkeypatch):
    monkeypatch.setattr("app.fnos.sync_playlist", lambda n, p: {"status": "ok"})
    r = _api_client().post("/api/v1/fnos/playlists", json={"name": "单", "task_id": "不存在"})
    assert r.status_code == 400 and "不在内存中" in r.json()["detail"]


def test_api_append_requires_paths_or_guids():
    r = _api_client().post("/api/v1/fnos/playlists/单/tracks", json={})
    assert r.status_code == 400


def test_api_append_success(monkeypatch):
    captured = {}

    def _fake(name, container_paths=None, guids=None):
        captured.update(name=name, paths=container_paths, guids=guids)
        return {"status": "ok", "added": 1, "already": 0, "unresolved": []}

    monkeypatch.setattr("app.fnos.append_tracks", _fake)
    r = _api_client().post("/api/v1/fnos/playlists/单/tracks", json={"guids": ["g1"]})
    assert r.status_code == 200 and r.json()["added"] == 1
    assert captured == {"name": "单", "paths": None, "guids": ["g1"]}


def test_api_search_blank_400():
    r = _api_client().get("/api/v1/fnos/search", params={"q": "  "})
    assert r.status_code == 400


def test_api_search_suggest(monkeypatch):
    monkeypatch.setattr("app.fnos.search_suggest",
                        lambda q: {"track": {"total": 0, "items": []}})
    r = _api_client().get("/api/v1/fnos/search", params={"q": "王菲"})
    assert r.status_code == 200 and "track" in r.json()


def test_api_search_tracks(monkeypatch):
    captured = {}

    def _fake(q, limit=50):
        captured.update(q=q, limit=limit)
        return {"total": 0, "returned": 0, "items": []}

    monkeypatch.setattr("app.fnos.search_tracks", _fake)
    r = _api_client().get("/api/v1/fnos/search/tracks", params={"q": "王菲", "limit": 20})
    assert r.status_code == 200
    assert captured == {"q": "王菲", "limit": 20}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -x -q`
Expected: FAIL（404 Not Found，路由未注册）

- [ ] **Step 3: 实现**

`app/main.py`——import 行调整：

```python
from .schemas import (..., FnosPlaylistAppendRequest, FnosPlaylistRequest)  # 并入现有 import
from . import fnos as fnos_svc  # 并入现有 `from . import ...` 组
```

`api_qq_auth_refresh` 之后（文件末尾）追加：

```python
# ---- 飞牛音乐歌单（原子管理 + 搜索；未配置 fnos_music → 400） ----

def _fnos_http(e: Exception) -> HTTPException:
    """fnos 异常 → HTTP：未配置 400 / 歌单不存在 404 / 其余（认证/API/网络）502。"""
    if isinstance(e, fnos_svc.FnosNotConfiguredError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, fnos_svc.FnosPlaylistNotFound):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=502, detail=f"飞牛音乐接口调用失败: {e}")


def _fnos_task_paths(task_id: str) -> list[str]:
    """取单曲任务成功入库曲目的容器路径：幂等重跑 archive_tracks 拿 target（任务须在内存）。"""
    task = dl.get(task_id)
    if not task:
        raise LookupError(f"任务 {task_id} 不在内存中（服务重启后请改用 paths 入参）")
    if not task.library:
        raise ValueError(f"任务 {task_id} 下载时未指定 library，无入库曲目，请改用 paths 入参")
    from .archive import archive_tracks, archived_container_paths
    return archived_container_paths(archive_tracks(task_id, library=task.library))


@app.get("/api/v1/fnos/playlists", dependencies=[Depends(auth)])
def api_fnos_playlists() -> list[dict]:
    try:
        return fnos_svc.list_playlists()
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/playlists/{name}/tracks", dependencies=[Depends(auth)])
def api_fnos_playlist_tracks(name: str) -> dict:
    try:
        return fnos_svc.playlist_detail(name)
    except Exception as e:
        raise _fnos_http(e)


@app.post("/api/v1/fnos/playlists", dependencies=[Depends(auth)])
def api_fnos_sync_playlist(req: FnosPlaylistRequest) -> dict:
    try:
        paths = list(req.paths or [])
        if req.task_id:
            paths.extend(_fnos_task_paths(req.task_id))
        return fnos_svc.sync_playlist(req.name, paths)
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _fnos_http(e)


@app.post("/api/v1/fnos/playlists/{name}/tracks", dependencies=[Depends(auth)])
def api_fnos_append_tracks(name: str, req: FnosPlaylistAppendRequest) -> dict:
    if not req.paths and not req.guids:
        raise HTTPException(status_code=400, detail="paths 与 guids 至少传其一")
    try:
        return fnos_svc.append_tracks(name, container_paths=req.paths, guids=req.guids)
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/search", dependencies=[Depends(auth)])
def api_fnos_search(q: str = "") -> dict:  # q 缺省/空白统一 400（规格口径，不走 FastAPI 422）
    if not q.strip():
        raise HTTPException(status_code=400, detail="q 不能为空")
    try:
        return fnos_svc.search_suggest(q)
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/search/tracks", dependencies=[Depends(auth)])
def api_fnos_search_tracks(q: str = "", limit: int = 50) -> dict:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q 不能为空")
    try:
        return fnos_svc.search_tracks(q, limit=limit)
    except Exception as e:
        raise _fnos_http(e)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_fnos.py -q`
Expected: 45 passed

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 198 passed）

```bash
git add app/main.py tests/test_fnos.py
git commit -m "feat: 飞牛歌单 REST 六端点（列/详情/建补/严格追加 + suggest/全量搜索，400/404/502 映射）"
```

---

### Task 6: MCP 七处（submit_download 加参 + 六工具）

**Files:**
- Modify: `mcp_adapter.py`

**Interfaces:**
- Consumes: Task 5 的六个 REST 路由 + `POST /api/v1/downloads` 的 `playlist` 字段
- Produces（MCP 工具名，Agent 侧可见）：
  `submit_download(…, playlist?)`、`list_fnos_playlists()`、`get_fnos_playlist_tracks(name)`、
  `create_fnos_playlist(name, task_id?, paths?)`、`add_fnos_playlist_tracks(name, paths?, guids?)`、
  `search_fnos(q)`、`search_fnos_tracks(q, limit?)`

- [ ] **Step 1: 实现 submit_download 加参**

`mcp_adapter.py` 的 `submit_download` 替换为：

```python
@mcp.tool()
def submit_download(tracks: list[dict], subdir: str | None = None, library: str | None = None,
                    max_size_mb: float | None = None, playlist: str | None = None) -> dict:
    """提交下载任务（异步）。

    Args:
        tracks: 待下载曲目列表。推荐每项只传 id 字段（取自 search_tracks / parse_playlist
            返回项的 id），如 [{"id": "KuwoMusicClient:594551679"}]，服务端按搜索缓存自动
            补全下载上下文；缓存有效期 1 小时，过期或服务重启后需重新搜索。
            也兼容直接回传 search_tracks 的完整返回项（含 raw），但没必要。
        subdir: 下载根目录下的子目录名，留空则按"时间戳_首曲名"自动组织
        library: 目标库名（可选，见 list_libraries）；传入则下载完成后自动归档到该库，
            单曲入库结构为 {库根}/{艺人}/{曲名.ext}（专辑请用 download_album + archive_album）
        max_size_mb: 单文件体积上限（MB，可选）；>0 时超限曲目跳过且优先于服务端配置，0/空不限
        playlist: 飞牛音乐歌单名（可选，必须搭配 library）：下载+自动归档完成后把成功入库
            曲目同步进该歌单（不存在则新建，已有曲目按 guid 去重）；需服务端配置 fnos_music。
            同步结果在任务的 playlist_result 字段（get_download_status 可见），
            失败不影响下载与归档
    Returns:
        task_id 等，用 get_download_status 轮询进度，以其 status/errors 为最终结果。
    """
    payload: dict[str, Any] = {"tracks": tracks}
    if subdir:
        payload["subdir"] = subdir
    if library:
        payload["library"] = library
    if max_size_mb:
        payload["max_size_mb"] = max_size_mb
    if playlist:
        payload["playlist"] = playlist
    with _client() as c:
        r = c.post("/api/v1/downloads", json=payload)
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "total": t["total"], "save_dir": t["save_dir"]}
```

- [ ] **Step 2: 实现六个新工具**

`mcp_adapter.py` 中 `backfill_lyrics` 工具之后（`if __name__ == "__main__":` 之前）追加：

```python
@mcp.tool()
def list_fnos_playlists() -> dict:
    """列出飞牛音乐的全部歌单（guid/name/track_count）。

    前置：服务端 config.yaml 已配置 fnos_music 段（飞牛音乐应用账号密码）。
    """
    with _client() as c:
        r = c.get("/api/v1/fnos/playlists")
        r.raise_for_status()
        items = r.json()
    return {"total": len(items), "playlists": items}


@mcp.tool()
def get_fnos_playlist_tracks(name: str) -> dict:
    """查看飞牛音乐指定歌单内的曲目（guid/title/artists/album/duration/path）。

    Args:
        name: 歌单名（精确匹配；不存在会返回 404 错误）
    """
    with _client() as c:
        r = c.get(f"/api/v1/fnos/playlists/{name}/tracks", timeout=300)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def create_fnos_playlist(name: str, task_id: str | None = None,
                         paths: list[str] | None = None) -> dict:
    """建/补飞牛音乐歌单（ensure 语义：不存在则新建；曲目按 guid 去重追加，幂等可重试）。

    Args:
        name: 歌单名
        task_id: 单曲下载任务 ID（可选；取其成功入库曲目。任务须在内存中且下载时传了
            library，服务重启后请改用 paths）
        paths: 容器内库文件绝对路径清单（可选，如 ["/singles/阿桑/叶子.flac"]）；
            与 task_id 可叠加；两者均缺时只建空歌单
    Returns:
        status（ok/partial）、playlist_guid、added（新加）、already（已存在去重）、
        unresolved（飞牛尚未扫描到的路径，稍后用同名调用重试即可，幂等）。
        新入库曲目依赖飞牛 watcher 扫描，服务端会轮询等待（scan_wait_s，默认 120s），
        本工具可能阻塞较久，属预期。
    """
    payload: dict[str, Any] = {"name": name}
    if task_id:
        payload["task_id"] = task_id
    if paths:
        payload["paths"] = paths
    with _client() as c:
        r = c.post("/api/v1/fnos/playlists", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def add_fnos_playlist_tracks(name: str, paths: list[str] | None = None,
                             guids: list[str] | None = None) -> dict:
    """严格追加曲目到既有飞牛歌单（歌单不存在返回 404，不会静默新建——防止打错字建错单）。

    Args:
        name: 既有歌单名（精确匹配）
        paths: 容器内库文件绝对路径清单（可选；经 path_map 解析为飞牛 guid）
        guids: 飞牛曲目 guid 清单（可选，免路径解析直达；可由 search_fnos /
            search_fnos_tracks 获得）。paths/guids 至少传其一
    Returns:
        同 create_fnos_playlist：status/added/already/unresolved。
    """
    payload: dict[str, Any] = {}
    if paths:
        payload["paths"] = paths
    if guids:
        payload["guids"] = guids
    with _client() as c:
        r = c.post(f"/api/v1/fnos/playlists/{name}/tracks", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def search_fnos(q: str) -> dict:
    """飞牛音乐库模糊搜索（suggest）：标题/艺人/专辑/歌单一把搜，各返回 top-5。

    Args:
        q: 搜索词（曲名/艺人/专辑/歌单名均可，跨字段模糊命中）
    Returns:
        track/album/artist/playlist 四组（track 项含 guid 与宿主 path）。
        找歌加歌单首选本工具；top-5 没中目标时用 search_fnos_tracks 全量翻。
    """
    with _client() as c:
        r = c.get("/api/v1/fnos/search", params={"q": q})
        r.raise_for_status()
        return r.json()


@mcp.tool()
def search_fnos_tracks(q: str, limit: int = 50) -> dict:
    """飞牛音乐库曲目全量搜索（带 guid，供 add_fnos_playlist_tracks 使用）。

    Args:
        q: 搜索词
        limit: 返回条数上限（默认 50，最大 200；响应 total 为飞牛侧全部匹配数）
    Returns:
        total/returned/items（guid/title/artists/album/duration/path）。
        典型流程：搜索挑 guid → add_fnos_playlist_tracks(歌单名, guids=[...])。
    """
    with _client() as c:
        r = c.get("/api/v1/fnos/search/tracks", params={"q": q, "limit": limit}, timeout=300)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 3: 冒烟验证（import + 工具枚举）**

Run:

```bash
.venv/bin/python -c "
import asyncio, mcp_adapter
tools = asyncio.run(mcp_adapter.mcp.get_tools())
assert len(tools) == 23, f'工具数 {len(tools)} != 23'
for name in ('list_fnos_playlists', 'get_fnos_playlist_tracks', 'create_fnos_playlist',
             'add_fnos_playlist_tracks', 'search_fnos', 'search_fnos_tracks'):
    assert name in tools, f'缺工具 {name}'
props = tools['submit_download'].parameters.get('properties', {})
assert 'playlist' in props, 'submit_download 缺 playlist 参数'
print('23 tools OK')"
```

Expected: 输出 `23 tools OK`（17 既有 + 6 新增）

- [ ] **Step 4: 全量回归 + Commit**

Run: `.venv/bin/python -m pytest -q`（Expected: 198 passed，mcp_adapter 无单测不受影响）

```bash
git add mcp_adapter.py
git commit -m "feat: MCP 飞牛歌单六工具 + submit_download 支持 playlist 参数"
```

---

### Task 7: 文档更新（README / API / MCP / ROADMAP / 规格状态）

**Files:**
- Modify: `README.md`、`docs/API.md`、`docs/MCP.md`、`ROADMAP.md`、`docs/superpowers/specs/2026-10-04-fnos-playlist-design.md`

- [ ] **Step 1: README.md**

`## REST API` 表格中 `POST /api/v1/downloads` 行改为：

```markdown
| POST | `/api/v1/downloads` | 提交下载（body：`{"tracks":[…], "subdir":?, "library":?, "max_size_mb":?, "playlist":?}`；传 library 则下载后自动归档；传 playlist（需配 fnos_music + library）则归档后自动同步飞牛歌单） |
```

该表格 `POST /api/v1/tracks/archive` 行后插入：

```markdown
| GET | `/api/v1/fnos/playlists` | 飞牛歌单列表（需配置 fnos_music） |
| GET | `/api/v1/fnos/playlists/{name}/tracks` | 飞牛歌单内曲目（不存在 404） |
| POST | `/api/v1/fnos/playlists` | 建/补飞牛歌单（body：`{"name":…, "task_id":?, "paths":?}`，ensure 语义幂等去重） |
| POST | `/api/v1/fnos/playlists/{name}/tracks` | 严格追加到既有飞牛歌单（body：`{"paths":?, "guids":?}`；歌单不存在 404） |
| GET | `/api/v1/fnos/search?q=…` | 飞牛库模糊搜索（track/album/artist/playlist 四组 top-5） |
| GET | `/api/v1/fnos/search/tracks?q=…&limit=…` | 飞牛曲目全量搜索（带 guid，供追加歌单挑选） |
```

`## MCP 工具` 表格中 `submit_download` 行改为：

```markdown
| `submit_download(tracks, subdir?, library?, max_size_mb?, playlist?)` | 提交下载（tracks 只传 `id`；传 library 下载后自动归档；传 playlist 归档后自动同步飞牛歌单） |
```

注：该行原文 "tracks 须含 `raw`" 与实际行为（只传 id 即可）不符，借本次一并修正。

该表格 `archive_tracks` 行后插入：

```markdown
| `list_fnos_playlists()` | 飞牛歌单列表 |
| `get_fnos_playlist_tracks(name)` | 飞牛歌单内曲目 |
| `create_fnos_playlist(name, task_id?, paths?)` | 建/补飞牛歌单（ensure 语义，幂等去重） |
| `add_fnos_playlist_tracks(name, paths?, guids?)` | 严格追加到既有飞牛歌单（404 防打错字） |
| `search_fnos(q)` | 飞牛库模糊搜索（四组 top-5） |
| `search_fnos_tracks(q, limit?)` | 飞牛曲目全量搜索（挑 guid 用） |
```

`## 外部依赖（按需）` 列表末尾追加：

```markdown
- **飞牛音乐歌单同步**：需在 `config.yaml` 配置 `fnos_music` 段（飞牛音乐应用账号密码 + `path_map` 容器库根→宿主路径映射）；纯 API 客户端，不挂载不读取 music.db
```

- [ ] **Step 2: docs/API.md**

`### DownloadTask（下载任务）` 表格 `library` 行后追加：

```markdown
| `playlist` | string \| null | 目标飞牛歌单名（传入时下载+归档完成后自动同步） |
| `playlist_result` | object \| null | 飞牛歌单同步结果：`status`（ok/partial/failed）、`playlist_guid`、`added`、`already`、`unresolved`、`error`；未同步为 null |
```

`### POST /api/v1/downloads` 请求体表格 `max_size_mb` 行后追加：

```markdown
| `playlist` | 否 | — | 飞牛音乐歌单名；**必须搭配 `library`**：下载+自动归档完成后把成功入库曲目同步进该歌单（不存在则新建，已有曲目按 guid 去重，失败隔离只记 `playlist_result`/`errors`）；需配置 `fnos_music` |
```

该节 **400** 说明改为：

```markdown
**响应 200**：`DownloadTask`；**400**：`tracks` 为空 / 未知库名 / 全部曲目体积超限 / 仅传 `id` 但缓存未命中（需重新搜索）/ 单传 `playlist` 不带 `library` / 传 `playlist` 但未配置 `fnos_music`
```

`## 错误码` 一节之前插入六个新端点文档：

```markdown
### GET /api/v1/fnos/playlists

列出飞牛音乐的全部歌单。**前置**：`config.yaml` 配置 `fnos_music` 段。

**响应 200**

```json
[{"guid": "…", "name": "我的收藏", "track_count": 12}]
```

**错误**：未配置 `fnos_music` → 400；飞牛侧失败（含认证失败自动重登无效）→ 502

---

### GET /api/v1/fnos/playlists/{name}/tracks

查看歌单内曲目。**路径参数**：`name`（歌单名，精确匹配，同名取首个）。

**响应 200**

```json
{"playlist_guid": "…", "playlist_name": "我的收藏", "count": 1,
 "tracks": [{"guid": "…", "title": "叶子", "artists": ["阿桑"], "album": "…",
             "duration": 300000, "path": "/vol1/1000/Media/Singles/阿桑/叶子.flac"}]}
```

**错误**：歌单不存在 → 404；其余同上

---

### POST /api/v1/fnos/playlists

建/补歌单（ensure 语义：不存在则新建；曲目按 guid 去重追加，幂等可重试）。

**请求体**

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 歌单名 |
| `task_id` | 否 | 单曲下载任务 ID：取其成功入库曲目（任务须在内存中且下载时传了 `library`，服务重启后请改用 `paths`） |
| `paths` | 否 | 容器内库文件绝对路径清单（如 `/singles/阿桑/叶子.flac`）；与 `task_id` 可叠加；均缺只建空歌单 |

**响应 200**

```json
{"status": "ok", "playlist_guid": "…", "playlist_name": "榜单-2026-10",
 "added": 8, "already": 2, "unresolved": [], "error": null}
```

`status=partial` 时 `unresolved` 为飞牛尚未扫描到的路径（服务端已按 `scan_wait_s` 轮询等待），稍后以同名请求重试即可（幂等）。

**错误**：`task_id` 任务不在内存/未指定 library → 400；其余同列端点

---

### POST /api/v1/fnos/playlists/{name}/tracks

严格追加到既有歌单（**歌单不存在返回 404，不会静默新建**——防止打错字建错单；要"没有就建"用上面的 POST `/api/v1/fnos/playlists`）。

**请求体**

| 字段 | 必填 | 说明 |
|---|---|---|
| `paths` | 否 | 容器内库文件绝对路径清单（经 `path_map` 解析为飞牛 guid） |
| `guids` | 否 | 飞牛曲目 guid 清单（免路径解析直达；可由 `/api/v1/fnos/search/tracks` 获得）。`paths`/`guids` **至少传其一** |

**响应 200**：同建/补端点结构。

**错误**：`paths`/`guids` 均缺 → 400；歌单不存在 → 404

---

### GET /api/v1/fnos/search

飞牛音乐库模糊搜索（suggest）：标题/艺人/专辑/歌单一把搜，各返回 top-5。

**Query 参数**：`q`（必填，空白 → 400）

**响应 200**

```json
{"track": {"total": 1, "items": [{"guid": "…", "title": "我也不想这样",
   "artists": ["王菲"], "album": "只爱陌生人", "duration": 240000,
   "path": "/vol1/1000/Media/Music/王菲/我也不想这样.flac"}]},
 "album": {"total": 0, "items": []}, "artist": {"total": 0, "items": []},
 "playlist": {"total": 0, "items": []}}
```

---

### GET /api/v1/fnos/search/tracks

飞牛曲目全量搜索（fnos 侧无分页一次返全量，服务端字段裁剪 + limit 截断）：挑 guid 给追加端点用。

**Query 参数**：`q`（必填，空白 → 400）、`limit`（默认 50，最大 200）

**响应 200**：`{"total": 60, "returned": 50, "items": [{"guid", "title", "artists", "album", "duration", "path"}]}`

---
```

- [ ] **Step 3: docs/MCP.md**

`### submit_download(...)` 条目改为（签名加 playlist，末尾补一段）：

```markdown
### submit_download(tracks, subdir?, library?, max_size_mb?, playlist?)
提交下载任务（异步）。`tracks` 每项**只需传 `id` 字段**（取自 `search_tracks`/`parse_playlist` 返回项，如 `[{"id": "KuwoMusicClient:594551679"}]`），服务端按搜索缓存自动补全下载上下文（缓存 1 小时，服务重启后失效，未命中会报 400 提示重新搜索）。返回 `task_id`。传 `library` 时下载完成后**自动归档**到该库（单曲结构 `{库根}/{艺人}/{曲名.ext}`，一步到位）；`max_size_mb` 为单文件体积上限（MB），>0 时超限曲目跳过且优先于服务端配置，0/空不限。传 `playlist`（**必须搭配 `library`**，需服务端配置 `fnos_music`）时，自动归档完成后把成功入库曲目**同步进飞牛音乐歌单**（不存在则新建，已有曲目按 guid 去重）；同步结果在任务的 `playlist_result` 字段（`get_download_status` 可见），失败不影响下载与归档。
```

`### backfill_lyrics(...)` 条目之后、`## 五、典型调用流程` 之前插入：

```markdown
### list_fnos_playlists()
列出飞牛音乐全部歌单（guid/name/track_count）。前置：服务端配置 `fnos_music` 段。

### get_fnos_playlist_tracks(name)
查看飞牛歌单内曲目（guid/title/artists/album/duration/path）；歌单名精确匹配，不存在报错（404）。

### create_fnos_playlist(name, task_id?, paths?)
建/补飞牛歌单（ensure 语义：不存在则新建；按 guid 去重追加，幂等）。`task_id` 取单曲下载任务的成功入库曲目（任务须在内存）；`paths` 为容器内库文件绝对路径清单，两者可叠加、均缺只建空歌单。返回 `status`（ok/partial）/`added`/`already`/`unresolved`（飞牛未扫描到的路径，稍后同名重试即可）。新入库曲目依赖飞牛 watcher 扫描，可能阻塞至 `scan_wait_s`（默认 120s），属预期。

### add_fnos_playlist_tracks(name, paths?, guids?)
严格追加曲目到既有飞牛歌单：**歌单不存在报错（404），不会静默新建**（防止打错字建错单）。`paths` 为容器内路径（经 path_map 解析），`guids` 为飞牛曲目 guid（免解析直达，可由 `search_fnos`/`search_fnos_tracks` 获得），至少传其一。

### search_fnos(q)
飞牛音乐库模糊搜索（suggest）：标题/艺人/专辑/歌单一把搜，各返回 top-5（track 项含 guid 与宿主 path）。找歌加歌单首选；没中目标时用 `search_fnos_tracks`。

### search_fnos_tracks(q, limit?)
飞牛曲目全量搜索（带 guid）。`limit` 默认 50 最大 200；返回 `total`（飞牛侧全部匹配数）/`returned`/`items`。典型流程：搜索挑 guid → `add_fnos_playlist_tracks(歌单名, guids=[...])`。
```

`## 五、典型调用流程` 中 `### 榜单浏览与下载` 之后插入：

```markdown
### 飞牛歌单同步（榜单场景）

1. `get_chart_tracks(source="qq", chart_id="4", limit=50)` 拿曲目；
2. `submit_download(tracks=[{"id": t.id} for t in tracks], library="singles", playlist="榜单-巅峰榜-2026-10")`；
3. `get_download_status(task_id)` 轮询至完成，`playlist_result` 见同步结果（`unresolved` 非空说明个别曲目飞牛还没扫到，可用 `create_fnos_playlist("榜单-巅峰榜-2026-10", task_id=…)` 事后补，幂等）；
4. `list_fnos_playlists()` / `get_fnos_playlist_tracks("榜单-巅峰榜-2026-10")` 复核。

### 飞牛歌单原子管理（已有歌曲 → 既有歌单）

1. `search_fnos_tracks("我也不想这样")` 或已知容器路径 `/singles/阿桑/叶子.flac`；
2. `add_fnos_playlist_tracks("我的收藏", guids=[…])` 或 `paths=[…]`（歌单必须已存在）；
3. 新歌单管理旧歌：`create_fnos_playlist("怀旧金曲", paths=[…])`（没有就建）。
```

`## 六、故障排查` 列表追加：

```markdown
- **飞牛歌单接口报 502 提示检查账号密码**：token 失效后自动重登也失败，多为 `fnos_music` 的账号密码变更或应用被重置——核对 config.yaml 后删除服务端 `data/fnos_music_state.json` 再试；
```

- [ ] **Step 4: ROADMAP.md + 规格状态**

`ROADMAP.md` 的 `2i.` 条目之后、`3. **MoviePilot 薄客户端插件**` 之前插入：

```markdown
2j. **飞牛音乐歌单同步** ✅ 第一期已完成（2026-10-04）
   - 背景：下载入库的歌曲在飞牛音乐里只是散落在曲库中，缺歌单组织维度；典型场景：榜单批量下载后新建榜单歌单管理；以及把已有歌曲加入既有歌单的原子管理
   - 设计文档：[docs/superpowers/specs/2026-10-04-fnos-playlist-design.md](docs/superpowers/specs/2026-10-04-fnos-playlist-design.md)（接口契约 2026-10-04 本机实证：authx 不校验、search/suggest 四组 top-5、search/<type> 无分页全量返回）
   - 第一期：纯 API 客户端 `app/fnos.py`（password-login sha256+deviceId → userToken，music-token cookie，99999 惰性重登，token 持久化 `data/fnos_music_state.json` 原子写；不实现 authx、不读 music.db，guid 由 `track/list` 的 `audioSpec.path` 按 config `path_map` 精确解析，`scan_wait_s` 轮询等飞牛扫描）；`submit_download` 新增 `playlist` 参数（下载+归档后自动同步，失败隔离记 `playlist_result`）；REST 六端点 + MCP 六工具（歌单列/详情/建补 ensure/严格追加 404 + suggest/全量搜索）
   - 后续（另行立项）：`download_album` 流程 `playlist` 参数（专辑歌单）；歌单封面/删除/改名/单内曲目移除；token 定时体检
```

`docs/superpowers/specs/2026-10-04-fnos-playlist-design.md` 状态行改为：

```markdown
状态：已实施（2026-10-04，实施计划 docs/superpowers/plans/2026-10-04-fnos-playlist.md）
```

- [ ] **Step 5: Commit**

```bash
git add README.md docs/API.md docs/MCP.md ROADMAP.md docs/superpowers/specs/2026-10-04-fnos-playlist-design.md
git commit -m "docs: 飞牛歌单同步文档（README/API/MCP/ROADMAP 2j + 规格状态已实施）"
```

---

### Task 8: E2E 验证（A：本地 fnos API 冒烟；B：NAS 部署后全链路）

**Files:** 无代码改动（验证任务；发现缺陷另开修复 commit）

**前置（需要用户配合）**：
1. 用户提供飞牛音乐应用账号密码；Phase A 写入**本地临时配置**（如 `/tmp/fnos-e2e-config.yaml`，以项目 `config.yaml` 为底本加 `fnos_music` 段，`db_path` 改 `/tmp/fnos-e2e-data/music_service.db`），**严禁提交 git**；
2. `path_map` 实际值（NAS 容器 `/library`、`/singles` 对应的宿主路径前缀）——Phase A 用 guid 直达不强依赖，但 Phase B 必须正确；
3. Phase B 前：合并 main → 用户规则构建镜像打 tag（`docker compose build` + `docker tag media-music-service:latest yangdspig/media-music-service:latest`）→ 用户部署到 NAS。

- [ ] **Step 1: Phase A — 启动本地服务（端口 8875）**

```bash
mkdir -p /tmp/fnos-e2e-data
MUSIC_SERVICE_CONFIG=/tmp/fnos-e2e-config.yaml nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8875 > /tmp/fnos-e2e.log 2>&1 &
sleep 3 && curl -s http://127.0.0.1:8875/api/v1/health
```

Expected: `{"ok":true,...}`

- [ ] **Step 2: Phase A — password-login 首要验证（规格风险项）**

```bash
curl -s http://127.0.0.1:8875/api/v1/fnos/playlists
cat /tmp/fnos-e2e-data/fnos_music_state.json
```

Expected: 200 返回歌单数组（`[{"guid","name","track_count"},...]`）；状态文件含 `userToken/deviceId/username/login_at`。
**若 502 且提示登录失败**：检查实机 `password-login` 响应字段名（规格风险：userToken 字段名为逆向佐证未实测），用 curl 直连复核后修正 `app/fnos.py` 的 `login()`：

```bash
PW_HEX=$(printf '%s' '<密码>' | sha256sum | cut -d' ' -f1)
curl -sk -X POST 'https://fnos.example.com:5667/music/api/v1/user/password-login' \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"<账号>\",\"password\":\"$PW_HEX\",\"deviceId\":\"e2e-probe\"}" | head -c 500
```

- [ ] **Step 3: Phase A — 搜索与歌单原子操作全链路**

```bash
# 模糊搜索（四组结构 + track 含 guid/path）
curl -s 'http://127.0.0.1:8875/api/v1/fnos/search?q=王菲' | head -c 600
curl -s 'http://127.0.0.1:8875/api/v1/fnos/search/tracks?q=叶子&limit=3'
# 建单 → 追加（guid 直达）→ 去重复加 → 详情 → 404
curl -s -X POST http://127.0.0.1:8875/api/v1/fnos/playlists \
  -H 'Content-Type: application/json' -d '{"name":"测试-歌单同步"}'
curl -s -X POST 'http://127.0.0.1:8875/api/v1/fnos/playlists/测试-歌单同步/tracks' \
  -H 'Content-Type: application/json' -d '{"guids":["<上一步取得的 guid>"]}'
# 预期 added=1；原样再发一次 → already=1
curl -s 'http://127.0.0.1:8875/api/v1/fnos/playlists/测试-歌单同步/tracks'
# 预期 count=1 且曲目信息正确
curl -s -X POST 'http://127.0.0.1:8875/api/v1/fnos/playlists/不存在的歌单/tracks' \
  -H 'Content-Type: application/json' -d '{"guids":["x"]}'
# 预期 404
```

飞牛音乐 App 中确认「测试-歌单同步」出现且含目标曲目。

- [ ] **Step 4: Phase A — 清理测试歌单 + 停服务**

```bash
TOKEN=$(curl -sk -X POST 'https://fnos.example.com:5667/music/api/v1/user/password-login' \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"<账号>\",\"password\":\"$PW_HEX\",\"deviceId\":\"e2e-probe\"}" \
  | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['data']['userToken'])")
GUID=$(curl -sk 'https://fnos.example.com:5667/music/api/v1/playlist/list' \
  -H "Cookie: music-token=$TOKEN" \
  | .venv/bin/python -c "import sys,json;print([p['guid'] for p in json.load(sys.stdin)['data']['list'] if p['name']=='测试-歌单同步'][0])")
curl -sk -X POST 'https://fnos.example.com:5667/music/api/v1/playlist/delete' \
  -H "Cookie: music-token=$TOKEN" -H 'Content-Type: application/json' -d "{\"guid\":\"$GUID\"}"
# 若 delete 返回 code!=0（body 形式不符），改在飞牛 App 手动删除
kill %1  # 停 8875 服务
```

Expected: 测试歌单删除，飞牛 App 无残留；8875 进程停止。

- [ ] **Step 5: Phase B — NAS 部署后全链路（用户部署完成后执行）**

NAS 的 `config.yaml` 已填好 `fnos_music`（真实凭证 + 实际 path_map）：

```bash
# 1. MCP 工具枚举（容器内，23 工具）
ssh NAS 'cd <部署目录> && docker compose exec mcp-adapter python -c "import asyncio,mcp_adapter; print(len(asyncio.run(mcp_adapter.mcp.get_tools())))"'
# 2. 搜索两首单曲取 id → 带 playlist 提交下载
curl -s 'http://192.168.254.112:8765/api/v1/search?keyword=叶子&limit=3'
curl -s -X POST http://192.168.254.112:8765/api/v1/downloads -H 'Content-Type: application/json' \
  -d '{"tracks":[{"id":"<id1>"},{"id":"<id2>"}],"library":"singles","playlist":"测试-榜单歌单"}'
# 3. 轮询任务至 success，检查 message 含「歌单同步」、playlist_result.status 为 ok/partial
curl -s http://192.168.254.112:8765/api/v1/downloads/<task_id>
# 4. 原子端点复验
curl -s http://192.168.254.112:8765/api/v1/fnos/playlists
curl -s 'http://192.168.254.112:8765/api/v1/fnos/playlists/测试-榜单歌单/tracks'
# 5. 飞牛 App 验证歌单与曲目在列
# 6. 重登失败路径：NAS 上把 fnos_music.password 改错 → 删 data/fnos_music_state.json →
#    调 GET /api/v1/fnos/playlists 预期 502 且提示检查账号密码 → 恢复密码 → 复调 200
# 7. 飞牛 App 手动删除「测试-榜单歌单」
```

Expected: 全链路生效（下载→归档→歌单同步→App 可见），失败路径行为符合规格。

- [ ] **Step 6: E2E 记录 + Commit（如有文档/缺陷修复）**

E2E 中修复的缺陷各自单独 commit（中文 conventional）；最终 `.venv/bin/python -m pytest -q` 全绿后进入收尾流程（finishing-a-development-branch）。

---

## Self-Review 记录

自审发现并已 inline 修正的问题：

1. **测试计数修正**：初稿各 Task 预期通过数从 Task 1 起整体多算 1（Task 1 实为 10 个新测试
   而非 11）；已逐一修正为 T1=10/总 163、T2=18/171、T3=26/179、T4=32/185、T5=45/198
   （终态 198 = 基线 153 + 新增 45）。
2. **搜索端点 q 缺失的 400 语义**：规格要求「q 缺失或空白 → 400」，初稿 `q: str` 必填会在
   缺失时被 FastAPI 默认校验拦为 422；已改为 `q: str = ""` 由端点内统一判空白返 400。
3. **MCP 冒烟断言脆弱性**：初稿用 `tool.fn.__code__`（fastmcp 内部属性，版本间不稳）检查
   `submit_download` 签名；已改为公开 schema `tool.parameters['properties']` 断言。

规格覆盖核对（逐项）：认证状态机 / 状态持久化原子写 / 99999 惰性重登（T1）；歌单原语 /
path_map 最长前缀 / track/list 分页 + scan_wait 轮询（T2）；ensure/严格两语义编排 /
去重追加 / suggest 四组与全量搜索裁剪（T3）；submit_download playlist 参数 / 400 两例 /
playlist_result / 失败隔离（T4）；REST 六端点与 400/404/502 映射（T5）；MCP 七处（T6）；
README/API/MCP/ROADMAP + 规格状态（T7）；password-login 首要验证 / NAS 全链路 /
重登失败路径 / 测试歌单清理（T8）。非目标（不读 music.db / 不实现 authx / 不接
download_album / 不做封面删单改名）无越界。无遗漏。

类型一致性核对：`FnosClient` 方法与模块门面同名同签（`sync_playlist(name, container_paths)`、
`append_tracks(name, container_paths=None, guids=None)` 等）；`archived_container_paths`
单点定义于 archive.py 供 download 钩子与 main 的 `_fnos_task_paths` 共用；
`FnosPlaylistRequest/FnosPlaylistAppendRequest` 在 T4 定义、T5 消费；
异常族 `FnosNotConfiguredError/FnosAuthError/FnosApiError/FnosPlaylistNotFound`
在 `_fnos_http` 全覆盖（400/502/404）；T1 测试固件（`_client/_state/_router/_track`）
供 T2-T5 复用。一致。
