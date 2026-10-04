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
