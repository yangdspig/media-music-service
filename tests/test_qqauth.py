"""QQ 登录态保活单元测试：解析/映射/状态文件（网络全部 mock）。"""
import json
import time
from types import SimpleNamespace

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

    def fake_device():
        version = SimpleNamespace(release="10", sdk=29)
        return SimpleNamespace(version=version, android_id="aid16hex0000abcd",
                               model="MI 6", fingerprint="xiaomi/iarim/sagit:10/eomam:user/release-keys")

    monkeypatch.setattr(qqauth, "_device_context",
                        lambda: (fake_device(), "guid32hex", {"q16": "q16v", "q36": "q36v"}))
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


# ---- 设备上下文持久化（防 20279 设备数超限） ----

def test_keepalive_persists_device_context(state_dir, monkeypatch, mock_net):
    _seed_config(monkeypatch)
    monkeypatch.setattr(time, "time", lambda: 1788258930 + 259200 - 3600.0)
    _, responder = mock_net
    responder["post"].extend([{"req_0": {"code": 0, "data": {"session": {"uid": 1, "sid": "s"}}}},
                              _refresh_ok_payload()])
    responder["get"].append({"code": 0})
    assert qqauth.keepalive_once()["status"] == "refreshed"
    state = json.loads(qqauth._state_path().read_text())
    assert state["device"]["guid"] == "guid32hex"
    assert state["device"]["qimei"] == {"q16": "q16v", "q36": "q36v"}


def test_device_context_reused_from_state(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    qqauth._save_state(qqauth.QQCredential(), config_createtime="1788258930", expired=False,
                       device={"aid": "aid_saved", "phonetype": "MI 6", "rom": "rom_saved",
                               "os_ver": "10", "devicelevel": "29", "guid": "guid_saved",
                               "qimei": {"q16": "q16_saved", "q36": "q36_saved"}})
    # 已有持久化设备时不得再调 obtainqimei（会产生新设备）
    monkeypatch.setattr(qqauth.QQMusicClientUtils, "obtainqimei",
                        lambda *a: pytest.fail("不应重新获取 QIMEI"))
    device, guid, qimei = qqauth._device_context()
    assert guid == "guid_saved"
    assert qimei["q36"] == "q36_saved"
    assert device.android_id == "aid_saved"
    assert device.version.release == "10" and device.version.sdk == 29


def test_device_context_created_when_no_state(state_dir, monkeypatch):
    _seed_config(monkeypatch)
    monkeypatch.setattr(qqauth.QQMusicClientUtils, "obtainqimei",
                        lambda *a: {"q16": "new_q16", "q36": "new_q36"})
    device, guid, qimei = qqauth._device_context()
    assert qimei["q36"] == "new_q36" and len(guid) == 32
