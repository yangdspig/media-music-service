# QQ 音乐 Cookie 保活实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** QQ 音乐源 cookies 自动保活——musickey 到期前自动刷新，彻底失效时明确提示用户重新粘贴。

**Architecture:** 新增 `app/qqauth.py`（凭证解析/刷新/状态文件），registry 在每次 build_client 时注入有效 cookies（状态文件优先于 config.yaml），后台线程每 1h 检查、剩余 <24h 刷新，REST 提供手动触发。

**Tech Stack:** Python 3.13, FastAPI, pytest, musicdl（复用其 Device/QIMEI/GetSession 工具），requests。零新增依赖。

## Global Constraints

- 不新增第三方依赖（requirements.txt 不变）
- 不改写 config.yaml；刷新产物写 `data/qq_auth_state.json`（与 db_path 同目录）
- 刷新接口 `expired_in`/`musicid` 必须传 **int**（str 会返回 code=10006）
- 测试全部 mock 网络（monkeypatch `qqauth._post` / `qqauth._get`），禁止真实外呼
- 测试命令：`.venv/bin/python -m pytest tests/ -q`
- 代码风格：模块 docstring 用中文、简述设计要点；注释克制，与现有 app/ 模块一致

---

### Task 1: qqauth 核心——凭证解析、cookie 映射、状态文件

**Files:**
- Create: `app/qqauth.py`
- Test: `tests/test_qqauth.py`

**Interfaces:**
- Produces（后续任务依赖这些名字，签名必须一致）:
  - `QQCredential` dataclass，字段（全有默认值，顺序即构造顺序）：
    `musicid: int = 0, musickey: str = "", openid: str = "", unionid: str = "", access_token: str = "", refresh_token: str = "", expired_at: int = 0, refresh_key: str = "", login_type: int = 0, musickey_createtime: int = 0, key_expires_in: int = 0`
  - `parse_cookie_str(raw: str) -> dict[str, str]`
  - `parse_credential(cookies: dict) -> QQCredential`
  - `credential_to_cookies(cred: QQCredential, base: dict) -> dict`
  - `effective_cookies() -> dict | None`（状态文件有效则返回刷新后 cookies，否则返回 None 表示用 config 原值）
  - `state_is_expired() -> bool`
  - `QQAuthRefreshError(Exception)`，属性 `code: int`
  - `QQAuthExpiredError(QQAuthRefreshError)`（致命子类）

- [ ] **Step 1: 写失败测试**

```python
"""QQ 登录态保活单元测试：解析/映射/状态文件（网络全部 mock）。"""
import json
import time

import pytest

from app import qqauth
from app.config import settings, SourceConfig


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """状态文件锚定到 db_path 同目录；用 tmp_path 隔离。"""
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "music_service.db"))
    return tmp_path


SAMPLE_COOKIES = {
    "uin": "417195563",
    "qqmusic_key": "Q_H_L_old",
    "qm_keyst": "Q_H_L_old",
    "psrf_qqopenid": "openid123",
    "psrf_qqunionid": "unionid123",
    "psrf_qqaccess_token": "at123",
    "psrf_qqrefresh_token": "rt123",
    "psrf_access_token_expiresAt": "1793442930",  # str，必须转 int
    "psrf_musickey_createtime": "1788258930",
    "tmeLoginType": "2",
    "pgv_pvid": "irrelevant",
}


def test_parse_cookie_str():
    d = qqauth.parse_cookie_str("a=1; b=x=y; empty=; 空格 = v ")
    assert d == {"a": "1", "b": "x=y", "empty": "", "空格": "v"}


def test_parse_credential_int_coercion():
    cred = qqauth.parse_credential(SAMPLE_COOKIES)
    assert cred.musicid == 417195563 and isinstance(cred.musicid, int)
    assert cred.expired_at == 1793442930 and isinstance(cred.expired_at, int)
    assert cred.musickey_createtime == 1788258930
    assert cred.login_type == 2
    assert cred.musickey == "Q_H_L_old"
    assert cred.refresh_token == "rt123"
    assert cred.refresh_key == ""  # 浏览器 cookies 没有此字段


def test_parse_credential_missing_fields():
    cred = qqauth.parse_credential({})
    assert cred.musicid == 0 and cred.musickey == "" and cred.expired_at == 0


def test_credential_to_cookies_maps_all_fields():
    cred = qqauth.QQCredential(musicid=417195563, musickey="Q_H_L_new",
                               access_token="at_new", refresh_token="rt_new",
                               expired_at=1793500000, musickey_createtime=1788300000)
    out = qqauth.credential_to_cookies(cred, dict(SAMPLE_COOKIES))
    assert out["qqmusic_key"] == "Q_H_L_new"
    assert out["qm_keyst"] == "Q_H_L_new"          # base 里有就同步更新
    assert out["psrf_qqaccess_token"] == "at_new"
    assert out["psrf_qqrefresh_token"] == "rt_new"
    assert out["psrf_access_token_expiresAt"] == "1793500000"   # 回写为 str
    assert out["psrf_musickey_createtime"] == "1788300000"
    assert out["pgv_pvid"] == "irrelevant"         # 无关字段原样保留
    assert out["uin"] == "417195563"


def test_effective_cookies_no_config(state_dir, monkeypatch):
    monkeypatch.setattr(settings, "sources", {})
    assert qqauth.effective_cookies() is None


def _seed_config(monkeypatch):
    raw = "; ".join(f"{k}={v}" for k, v in SAMPLE_COOKIES.items())
    monkeypatch.setattr(settings, "sources",
                        {"QQMusicClient": SourceConfig(search_cookies=raw)})


def test_effective_cookies_no_state_falls_back_to_config(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    assert qqauth.effective_cookies() is None  # 无状态文件 → None，调用方用 config 原值


def test_effective_cookies_state_wins(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(musicid=417195563, musickey="Q_H_L_new",
                                           musickey_createtime=1788300000),
                       config_createtime="1788258930", expired=False)
    out = qqauth.effective_cookies()
    assert out is not None and out["qqmusic_key"] == "Q_H_L_new"


def test_state_reset_when_user_repastes(state_dir, monkeypatch):
    """用户重新粘贴 cookies（createtime 变化）→ 状态文件作废，回退 config。"""
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(musickey="Q_H_L_new"),
                       config_createtime="1111111111", expired=True)  # 陈旧种子
    assert qqauth.effective_cookies() is None
    assert qqauth.state_is_expired() is False  # 新 cookies 不受旧 expired 标记影响


def test_state_is_expired(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    assert qqauth.state_is_expired() is False
    qqauth._save_state(qqauth.QQCredential(), config_createtime="1788258930", expired=True)
    assert qqauth.state_is_expired() is True


def test_corrupt_state_file_falls_back(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._state_path().write_text("not json{{{")
    assert qqauth.effective_cookies() is None
    assert qqauth.state_is_expired() is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py -q`
Expected: FAIL（`ModuleNotFoundError: app.qqauth`）

- [ ] **Step 3: 实现 app/qqauth.py（本任务只含非网络部分）**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py -q`
Expected: PASS（9 项）

- [ ] **Step 5: Commit**

```bash
git add app/qqauth.py tests/test_qqauth.py
git commit -m "feat: qqauth 凭证解析与状态文件（cookie 保活 Task 1）"
```

---

### Task 2: qqauth 网络层——check_expired / refresh / keepalive_once

**Files:**
- Modify: `app/qqauth.py`（追加）
- Test: `tests/test_qqauth.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `QQCredential`、`parse_credential`、`credential_to_cookies`、`_save_state`、`_config_cookies`、`_state_matches_config`、`QQAuthRefreshError`、`QQAuthExpiredError`、常量 `_FATAL_CODES`、`_REFRESH_THRESHOLD_S`、`_DEFAULT_KEY_EXPIRES_IN`
- Produces:
  - `check_expired(cred: QQCredential) -> bool | None`（None=网络失败）
  - `refresh(cred: QQCredential) -> QQCredential`（抛 QQAuthRefreshError / QQAuthExpiredError）
  - `keepalive_once(force: bool = False) -> dict`（返回 `{"status": ...}`，status ∈ skipped/fresh/refreshed/expired/failed）

**测试设计说明**：模块内提供两个可 monkeypatch 的网络包裹函数 `_post(payload, ua) -> dict` 和 `_get(url, params, cookies) -> dict`，以及 `_device_context() -> tuple[Device, str, dict]`（device, guid, qimei）。测试全部 patch 这三者 + `time.time`。

- [ ] **Step 1: 追加失败测试**

```python
# ---- 网络层（_post/_get/_device_context 全部 monkeypatch） ----

@pytest.fixture()
def mock_net(monkeypatch):
    """拦截网络层，记录调用，返回可编程响应。"""
    calls = {"post": [], "get": []}
    responder = {"post": [], "get": []}  # 每次调用弹一个响应

    def fake_post(payload, ua):
        calls["post"].append(payload)
        return responder["post"].pop(0)

    def fake_get(url, params, cookies):
        calls["get"].append((url, params))
        return responder["get"].pop(0)

    monkeypatch.setattr(qqauth, "_post", fake_post)
    monkeypatch.setattr(qqauth, "_get", fake_get)
    monkeypatch.setattr(qqauth, "_device_context",
                        lambda: (object(), "guid32hex", {"q16": "q16v", "q36": "q36v"}))
    return calls, responder


def _refresh_ok_payload():
    return {"req_0": {"code": 0, "data": {
        "musickey": "Q_H_L_new", "refresh_key": "rk_new",
        "refresh_token": "rt_new", "access_token": "at_new",
        "expired_at": 1793500000, "musicid": 417195563,
        "musickeyCreateTime": 1788300000, "keyExpiresIn": 259200,
    }}}


def test_check_expired_valid(mock_net):
    _, responder = mock_net
    responder["get"].append({"code": 0})
    assert qqauth.check_expired(qqauth.QQCredential(musicid=1, musickey="k")) is False


def test_check_expired_expired(mock_net):
    _, responder = mock_net
    responder["get"].append({"code": 7})
    assert qqauth.check_expired(qqauth.QQCredential(musicid=1, musickey="k")) is True


def test_refresh_success_returns_new_credential(mock_net):
    calls, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              _refresh_ok_payload()])
    cred = qqauth.QQCredential(musicid=417195563, musickey="Q_H_L_old",
                               openid="openid123", access_token="at123",
                               refresh_token="rt123", expired_at=1793442930, login_type=2)
    new = qqauth.refresh(cred)
    assert new.musickey == "Q_H_L_new"
    assert new.refresh_key == "rk_new"            # 服务端下发的新 refresh_key 必须保留
    assert new.refresh_token == "rt_new"
    assert new.key_expires_in == 259200
    assert new.musickey_createtime == 1788300000
    # 关键回归：expired_in / musicid 必须是 int（str 会被服务端拒为 10006）
    login_param = calls["post"][1]["req_0"]["param"]
    assert isinstance(login_param["expired_in"], int)
    assert isinstance(login_param["musicid"], int)
    assert login_param["loginMode"] == 2


def test_refresh_expired_raises(mock_net):
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              {"req_0": {"code": 1000, "data": {}}}])
    with pytest.raises(qqauth.QQAuthExpiredError):
        qqauth.refresh(qqauth.QQCredential(musicid=1, musickey="k"))


def test_refresh_retryable_error(mock_net):
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              {"req_0": {"code": 10006, "data": {}}}])
    with pytest.raises(qqauth.QQAuthRefreshError) as exc:
        qqauth.refresh(qqauth.QQCredential(musicid=1, musickey="k"))
    assert not isinstance(exc.value, qqauth.QQAuthExpiredError)
    assert exc.value.code == 10006


# ---- keepalive_once 决策 ----

def test_keepalive_skipped_without_cookies(state_dir, monkeypatch):
    monkeypatch.setattr(settings, "sources", {})
    assert qqauth.keepalive_once()["status"] == "skipped"


def test_keepalive_fresh_no_refresh(state_dir, monkeypatch, mock_net):
    _seed_config(monkeypatch)  # createtime=1788258930，key_expires_in 未知→默认 3 天
    monkeypatch.setattr(time, "time", lambda: 1788258930 + 3600.0)  # 刚粘贴 1h，远未到期
    assert qqauth.keepalive_once()["status"] == "fresh"
    assert mock_net[0]["post"] == []  # 不应发起任何请求


def test_keepalive_refreshes_near_expiry(state_dir, monkeypatch, mock_net):
    _seed_config(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1788258930 + 259200 - 3600.0)  # 到期前 1h
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              _refresh_ok_payload()])
    responder["get"].append({"code": 0})  # 刷新后复核
    result = qqauth.keepalive_once()
    assert result["status"] == "refreshed"
    # 状态文件已写，effective_cookies 返回新 key
    out = qqauth.effective_cookies()
    assert out["qqmusic_key"] == "Q_H_L_new" and out["psrf_qqrefresh_token"] == "rt_new"


def test_keepalive_expired_marks_state(state_dir, monkeypatch, mock_net):
    _seed_config(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1788258930 + 259200 - 3600.0)
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              {"req_0": {"code": 104400, "data": {}}}])
    assert qqauth.keepalive_once()["status"] == "expired"
    assert qqauth.state_is_expired() is True


def test_keepalive_refresh_check_failed_keeps_old(state_dir, monkeypatch, mock_net):
    """刷新返回新 key 但复核不通过 → 不落盘，保留旧凭证。"""
    _seed_config(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1788258930 + 259200 - 3600.0)
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              _refresh_ok_payload()])
    responder["get"].append({"code": 7})  # 复核：新 key 无效
    assert qqauth.keepalive_once()["status"] == "failed"
    assert qqauth.effective_cookies() is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py -q`
Expected: FAIL（`AttributeError: check_expired` 等）

- [ ] **Step 3: 在 app/qqauth.py 追加网络层实现**

```python
# ---- 网络层（单独成函数便于测试 monkeypatch） ----

def _post(payload: dict, ua: str) -> dict:
    r = requests.post(_ENDPOINT, json=payload, headers={"User-Agent": ua}, timeout=15)
    r.raise_for_status()
    return r.json()


def _get(url: str, params: dict, cookies: dict) -> dict:
    r = requests.get(url, params=params, cookies=cookies,
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                                            "Chrome/148.0.0.0 Safari/537.36",
                              "Referer": "https://y.qq.com/"}, timeout=15)
    r.raise_for_status()
    return r.json()


def _device_context():
    """生成一次性设备上下文：设备指纹 + guid + QIMEI（复用 musicdl 实现）。"""
    device = Device()
    guid = QQMusicClientUtils.randomguid()
    qimei = QQMusicClientUtils.obtainqimei(_APP_VERSION, device)
    return device, guid, qimei


def check_expired(cred: QQCredential) -> bool | None:
    """WEB 端 profile 接口检测 musickey 有效性；None 表示检测请求本身失败。"""
    params = {"g_tk": str(QQMusicClientUtils.hash33(cred.musickey, 5381)),
              "format": "json", "inCharset": "utf-8", "outCharset": "utf-8",
              "notice": "0", "cid": "205360838", "needNewCode": "0",
              "loginUin": str(cred.musicid), "hostUin": "0",
              "userid": str(cred.musicid), "reqfrom": "1"}
    cookies = {"uin": str(cred.musicid), "qqmusic_uin": str(cred.musicid),
               "qqmusic_key": cred.musickey, "qm_keyst": cred.musickey}
    try:
        return _get(_CHECK_URL, params, cookies).get("code") != 0
    except Exception:
        logger.warning("QQ 凭证有效性检测请求失败", exc_info=True)
        return None


def refresh(cred: QQCredential) -> QQCredential:
    """ANDROID 协议栈刷新 musickey。彻底失效抛 QQAuthExpiredError，其余失败抛 QQAuthRefreshError。"""
    device, guid, qimei = _device_context()
    ua = f"QQMusic {_APP_CV}(android {device.version.release})"
    comm = {"ct": 11, "cv": _APP_CV, "v": _APP_CV, "tmeAppID": "qqmusic", "chid": "10003505",
            "qq": str(cred.musicid), "authst": cred.musickey, "tmeLoginType": cred.login_type,
            "QIMEI": qimei["q16"], "QIMEI36": qimei["q36"],
            "OpenUDID": guid, "OpenUDID2": guid, "udid": guid,
            "aid": device.android_id, "os_ver": device.version.release, "phonetype": device.model,
            "devicelevel": str(device.version.sdk), "newdevicelevel": str(device.version.sdk),
            "rom": device.fingerprint}
    sess = (_post({"comm": comm, "req_0": {"module": "music.getSession.session",
                                           "method": "GetSession",
                                           "param": {"uid": "", "vkey": 0, "caller": 0}}}, ua)
            .get("req_0") or {}).get("data") or {}
    session = sess.get("session") or {}
    comm.update(uid=str(session.get("uid", "")), sid=session.get("sid", ""))
    param = {"openid": cred.openid, "access_token": cred.access_token,
             "refresh_token": cred.refresh_token, "expired_in": cred.expired_at,  # 必须 int
             "musicid": cred.musicid, "musickey": cred.musickey,                  # 必须 int
             "refresh_key": cred.refresh_key, "loginMode": 2}
    login = _post({"comm": comm, "req_0": {"module": "music.login.LoginServer",
                                           "method": "Login", "param": param}}, ua).get("req_0") or {}
    code, data = login.get("code", -1), login.get("data") or {}
    if code == 0 and data.get("musickey"):
        return QQCredential(
            musicid=_int(data.get("musicid"), cred.musicid),
            musickey=data["musickey"],
            openid=data.get("openid") or cred.openid,
            unionid=data.get("unionid") or cred.unionid,
            access_token=data.get("access_token") or cred.access_token,
            refresh_token=data.get("refresh_token") or cred.refresh_token,
            expired_at=_int(data.get("expired_at"), cred.expired_at),
            refresh_key=data.get("refresh_key") or cred.refresh_key,
            login_type=cred.login_type,
            musickey_createtime=_int(data.get("musickeyCreateTime")) or int(time.time()),
            key_expires_in=_int(data.get("keyExpiresIn"), _DEFAULT_KEY_EXPIRES_IN),
        )
    if code in _FATAL_CODES:
        raise QQAuthExpiredError(code)
    raise QQAuthRefreshError(code)


# ---- 保活决策（周期任务与手动触发共用） ----

def keepalive_once(force: bool = False) -> dict:
    """单次保活：剩余有效期 <24h（或 force）时刷新；结果落状态文件。"""
    config = _config_cookies()
    if config is None:
        return {"status": "skipped", "reason": "未配置 QQ cookies"}
    state = _load_state()
    if state and not state.get("expired") and _state_matches_config(state, config):
        cred = QQCredential(**state["credential"])
    else:
        cred = parse_credential(config)
    now = int(time.time())
    remaining = cred.musickey_createtime + (cred.key_expires_in or _DEFAULT_KEY_EXPIRES_IN) - now
    if not force and remaining >= _REFRESH_THRESHOLD_S:
        return {"status": "fresh", "remaining_s": remaining}
    config_createtime = str(_int(config.get("psrf_musickey_createtime")))
    try:
        new_cred = refresh(cred)
    except QQAuthExpiredError as e:
        _save_state(cred, config_createtime, expired=True)
        logger.error("QQ 凭证彻底失效（code=%s），需重新粘贴 cookies", e.code)
        return {"status": "expired", "code": e.code}
    except Exception as e:
        logger.warning("QQ 凭证刷新失败，下周期重试: %s", e)
        return {"status": "failed", "error": str(e)}
    if check_expired(new_cred) is not False:
        logger.error("QQ 凭证刷新后复核不通过，保留旧凭证")
        return {"status": "failed", "error": "刷新后复核不通过"}
    _save_state(new_cred, config_createtime, expired=False)
    logger.info("QQ 凭证刷新成功，musickey 有效期 %ds", new_cred.key_expires_in)
    return {"status": "refreshed", "key_expires_in": new_cred.key_expires_in}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py -q`
Expected: PASS（累计 18 项）

- [ ] **Step 5: Commit**

```bash
git add app/qqauth.py tests/test_qqauth.py
git commit -m "feat: qqauth 刷新链路（GetSession→Login）与保活决策（Task 2）"
```

---

### Task 3: 配置项 + registry 集成

**Files:**
- Modify: `app/config.py`（AuthRefreshConfig）
- Modify: `app/registry.py:78-94`（`_build_init_cfg` 用有效 cookies）、`app/registry.py:50-75`（`list_sources` 凭证状态）
- Test: `tests/test_qqauth.py`（追加 registry 相关）

**Interfaces:**
- Consumes: Task 1 的 `qqauth.effective_cookies()` / `qqauth.state_is_expired()` / `qqauth.effective_credential_expiry()`
- Produces: `Settings.auth_refresh: AuthRefreshConfig`，字段 `enabled: bool = True`、`interval_s: int = 3600`
- Produces: `qqauth.effective_credential_expiry() -> int | None`（当前有效凭证的 musickey 到期时间戳，供 registry 展示）

- [ ] **Step 1: 追加失败测试**

```python
# ---- registry 集成 ----

def test_registry_uses_state_cookies(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(musicid=417195563, musickey="Q_H_L_new",
                                           musickey_createtime=1788300000),
                       config_createtime="1788258930", expired=False)
    from app import registry
    init_cfg = registry._build_init_cfg()
    qq_cfg = init_cfg["QQMusicClient"]
    assert "Q_H_L_new" in qq_cfg["default_search_cookies"]
    assert "Q_H_L_new" in qq_cfg["default_download_cookies"]


def test_registry_falls_back_to_config_without_state(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    from app import registry
    init_cfg = registry._build_init_cfg()
    assert "Q_H_L_old" in init_cfg["QQMusicClient"]["default_search_cookies"]


def test_list_sources_expired_marks_unavailable(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(), config_createtime="1788258930", expired=True)
    from app import registry
    entry = next(s for s in registry.list_sources() if s["name"] == "QQMusicClient")
    assert entry["available"] is False
    assert "重新粘贴" in entry["note"]


def test_list_sources_keepalive_note(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(musicid=1, musickey="k", musickey_createtime=1788300000,
                                           key_expires_in=259200),
                       config_createtime="1788258930", expired=False)
    from app import registry
    entry = next(s for s in registry.list_sources() if s["name"] == "QQMusicClient")
    assert entry["available"] is True
    assert "自动保活" in entry["note"]
```

注意：这两个 registry 测试要求 `_build_init_cfg` 返回的 cookie 值是 **str**（`"; ".join(...)` 序列化），因为 musicdl 的 init cfg 接受 cookie 字符串。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py -q`
Expected: FAIL（`Q_H_L_old` 而非 `Q_H_L_new` / note 不符）

- [ ] **Step 3: 实现**

`app/config.py` — 在 `CleanupConfig` 后追加，`Settings` 加字段：

```python
class AuthRefreshConfig(BaseModel):
    """QQ 音乐登录态自动保活。状态文件写 db_path 同目录的 qq_auth_state.json。"""
    enabled: bool = True  # 总开关
    interval_s: int = 3600  # 检查周期（秒），默认 1 小时；剩余有效期 <24h 才实际刷新
```

```python
    auth_refresh: AuthRefreshConfig = AuthRefreshConfig()  # QQ cookie 自动保活
```

（加到 `Settings` 的 `cleanup` 字段后面。）

`app/qqauth.py` — 追加供 registry 使用的辅助函数：

```python
def effective_credential_expiry() -> int | None:
    """当前有效凭证的 musickey 到期时间戳；无凭证/无状态返回 None。"""
    config = _config_cookies()
    if config is None:
        return None
    state = _load_state()
    if state and not state.get("expired") and _state_matches_config(state, config):
        cred = QQCredential(**state["credential"])
    else:
        cred = parse_credential(config)
    if not cred.musickey_createtime:
        return None
    return cred.musickey_createtime + (cred.key_expires_in or _DEFAULT_KEY_EXPIRES_IN)
```

`app/registry.py` — 顶部 import 改为延迟引用避免环（qqauth 只依赖 config，直接 import 即可）：

```python
from . import qqauth
```

`_build_init_cfg()` 在 `for name, cfg in settings.sources.items():` 循环内、`c.update(cfg.extra or {})` 之前插入：

```python
        if name == qqauth.QQ_SOURCE:
            eff = qqauth.effective_cookies()
            if eff:
                merged = "; ".join(f"{k}={v}" for k, v in eff.items())
                c["default_search_cookies"] = merged
                c["default_download_cookies"] = merged
```

注意：config 里 QQ 只配了 `search_cookies` 时，musicdl 的 download 阶段复用 search cookies（musicdl `default_cookies` 行为依赖各键注入，此处与现状一致——原来也只注入配了的键；保活状态下两个键都写，因为刷新产物对两者都有效）。

`list_sources()` 在 `out.append(...)` 之前、`note` 已定后追加 QQ 专属状态：

```python
        if name == qqauth.QQ_SOURCE and cfg and (cfg.search_cookies or cfg.download_cookies):
            if qqauth.state_is_expired():
                available, note = False, "登录凭证已失效且自动刷新失败，需重新粘贴 cookies"
            elif settings.auth_refresh.enabled:
                expiry = qqauth.effective_credential_expiry()
                if expiry:
                    note = time.strftime("自动保活中，musickey 有效期至 %Y-%m-%d %H:%M",
                                         time.localtime(expiry))
```

（`app/registry.py` 顶部加 `import time`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（全量，含旧测试 62 项 + 新 22 项）

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/qqauth.py app/registry.py tests/test_qqauth.py
git commit -m "feat: registry 接入保活 cookies 与凭证状态展示（Task 3）"
```

---

### Task 4: 启动钩子 + REST 手动触发

**Files:**
- Modify: `app/qqauth.py`（追加 `start_keepalive`）
- Modify: `app/main.py:21-25`（startup 钩子）、`app/main.py`（新路由）
- Test: `tests/test_qqauth.py`（追加 REST 测试）

**Interfaces:**
- Consumes: `qqauth.keepalive_once(force)`、`settings.auth_refresh`
- Produces: `qqauth.start_keepalive() -> None`；REST `POST /api/v1/auth/qq/refresh` → `{"status": ...}`（keepalive_once 的返回值原样返回）

- [ ] **Step 1: 追加失败测试**

```python
# ---- REST 手动触发 ----

def test_manual_refresh_endpoint(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    monkeypatch.setattr(qqauth, "keepalive_once",
                        lambda force=False: {"status": "refreshed", "forced": force})
    from fastapi.testclient import TestClient
    from app.main import app
    resp = TestClient(app).post("/api/v1/auth/qq/refresh")
    assert resp.status_code == 200
    assert resp.json()["status"] == "refreshed"
    assert resp.json()["forced"] is True
```

（若 conftest/已有测试已建 TestClient 模式则沿用；api_key 默认为空无需鉴权头。）

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_qqauth.py::test_manual_refresh_endpoint -q`
Expected: FAIL（404）

- [ ] **Step 3: 实现**

`app/qqauth.py` 追加：

```python
# ---- 周期任务 ----

def _keepalive_loop() -> None:
    while True:
        time.sleep(settings.auth_refresh.interval_s)
        try:
            keepalive_once()
        except Exception:
            logger.exception("QQ 凭证保活周期任务异常")


def start_keepalive() -> None:
    """服务启动时调用：按配置开启 QQ 凭证保活后台线程。"""
    if not settings.auth_refresh.enabled:
        return
    threading.Thread(target=_keepalive_loop, daemon=True, name="qq-auth-keepalive").start()
```

`app/main.py` startup 钩子追加一行：

```python
    from . import qqauth
    qqauth.start_keepalive()  # QQ 音乐登录态自动保活（按 config.yaml auth_refresh 段）
```

`app/main.py` 在 `api_history` 后追加路由：

```python
@app.post("/api/v1/auth/qq/refresh", dependencies=[Depends(auth)])
def api_qq_auth_refresh() -> dict:
    """手动触发一次 QQ 凭证刷新（强制，不看剩余有效期）。"""
    from . import qqauth
    return qqauth.keepalive_once(force=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（全量）

- [ ] **Step 5: Commit**

```bash
git add app/qqauth.py app/main.py tests/test_qqauth.py
git commit -m "feat: QQ 凭证保活周期任务与手动刷新接口（Task 4）"
```

---

### Task 5: 真实链路验证 + 文档收尾

**Files:**
- Modify: `config.yaml`（新增 auth_refresh 段注释示例）
- Modify: `docs/API.md`（新端点）
- Modify: `ROADMAP.md`（标记本条完成）
- Modify: `README.md`（若 cookies 配置说明涉及，补一句保活说明）

**Interfaces:**
- Consumes: 全部前序任务

- [ ] **Step 1: 真实链路冒烟（用 /vol1 真实配置，只读+刷新一次）**

```bash
.venv/bin/python -c "
from app import qqauth
print(qqauth.keepalive_once(force=True))
print('expiry:', qqauth.effective_credential_expiry())
"
```

注意：此命令用的是仓库 config.yaml（无 cookies）→ 预期 `skipped`。真实验证用：
`MUSIC_SERVICE_CONFIG=/vol1/1000/media-music-service/config.yaml .venv/bin/python -c "..."`
预期 `{"status": "refreshed", ...}`，且 `/vol1/1000/media-music-service/data/qq_auth_state.json` 生成。

- [ ] **Step 2: config.yaml 加示例段**

```yaml
auth_refresh:
  enabled: true        # QQ 音乐登录态自动保活（musickey 3 天到期，剩余 <24h 自动刷新）
  interval_s: 3600     # 检查周期（秒）
```

- [ ] **Step 3: docs/API.md 补端点说明**

在合适位置（/api/v1/sources 附近）追加：

```
### POST /api/v1/auth/qq/refresh
手动强制刷新 QQ 音乐登录凭证。返回 {"status": "refreshed"|"fresh"|"skipped"|"expired"|"failed", ...}；
status=expired 表示凭证彻底失效，需重新粘贴 cookies 到 config.yaml。
```

并在 `/api/v1/sources` 说明里补一句：QQ 源 note 会展示自动保活状态/失效提示。

- [ ] **Step 4: ROADMAP.md 追加完成条目**（沿用现有条目格式）

- [ ] **Step 5: 全量测试 + Commit**

Run: `.venv/bin/python -m pytest tests/ -q`

```bash
git add config.yaml docs/API.md ROADMAP.md README.md
git commit -m "docs: QQ cookie 保活配置示例与 API 文档（Task 5）"
```

---

## Self-Review 记录

- Spec 覆盖：解析/映射（T1）✓ 状态文件+种子重置（T1）✓ check_expired/refresh（T2）✓ 周期任务（T4）✓ registry 集成（T3）✓ 手动触发（T4）✓ 配置段（T3+T5）✓ sources note（T3）✓ 失败分类（T2）✓ 刷新后复核（T2 keepalive_once）✓
- 无占位符；类型一致性：`_save_state(cred, config_createtime, expired)` 三参数在 T1 定义、T2/T3 测试均按此调用 ✓；`_seed_config` 是测试模块级 helper，在 T1 定义、T2/T3 复用 ✓
- 遗留说明：spec 中"热更新内存 client"经核实不需要（build_client 每次操作新建实例），已在 spec 修正
