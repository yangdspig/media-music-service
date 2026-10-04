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
    """按 URL 后缀分发 FakeResp；payload 为 callable 时以 (method, url, kw) 调用，
    为 FakeResp 实例时直接透传。"""
    def _fake(method, url, **kw):
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                if callable(payload):
                    return payload(method, url, kw)
                if isinstance(payload, FakeResp):
                    return payload
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
