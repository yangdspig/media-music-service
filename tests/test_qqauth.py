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
