"""榜单目录（QQ/网易云排行榜）单元测试：httpx 层与 musicdl 客户端 mock，离线运行。"""
import httpx
import pytest

from app import charts
from app.schemas import ChartSummary, Track


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


QQ_LIST_RESP = {"code": 0, "subcode": 0, "data": {"topList": [
    {"id": 4, "topTitle": "巅峰榜·流行指数", "picUrl": "https://y.gtimg.cn/xxx.jpg",
     "listenCount": 123456789, "update": "2026-09-23",
     "songList": [{"songname": "歌曲A", "singername": "歌手甲"},
                  {"songname": "歌曲B", "singername": "歌手乙"}]},
    {"id": 26, "topTitle": "巅峰榜·热歌", "picUrl": "https://y.gtimg.cn/yyy.jpg",
     "listenCount": 98765432, "songList": []},
]}}


def test_qq_list_charts(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(QQ_LIST_RESP))
    out = charts.list_charts("qq")
    assert len(out) == 2
    c = out[0]
    assert isinstance(c, ChartSummary)
    assert c.id == "4"
    assert c.source == "qq"
    assert c.name == "巅峰榜·流行指数"
    assert c.cover_url == "https://y.gtimg.cn/xxx.jpg"
    assert c.track_count is None
    assert c.extra["listen_count"] == 123456789
    assert c.extra["preview"] == ["歌手甲-歌曲A", "歌手乙-歌曲B"]
    assert out[1].extra["preview"] == []


def test_qq_list_charts_error_code(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp({"code": -1}))
    with pytest.raises(LookupError):
        charts.list_charts("qq")


def test_list_charts_bad_source():
    with pytest.raises(ValueError):
        charts.list_charts("spotify")


NE_LIST_RESP = {"code": 200, "list": [
    {"id": 19723756, "name": "飙升榜", "coverImgUrl": "https://p2.music.126.net/aaa.jpg",
     "updateFrequency": "每日更新", "description": "每天更新"},
    {"id": 3779629, "name": "新歌榜", "coverImgUrl": "https://p2.music.126.net/bbb.jpg",
     "updateFrequency": "每日更新", "description": ""},
]}


def test_netease_list_charts(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(NE_LIST_RESP))
    out = charts.list_charts("netease")
    assert [(c.id, c.name) for c in out] == [("19723756", "飙升榜"), ("3779629", "新歌榜")]
    assert out[0].source == "netease"
    assert out[0].cover_url == "https://p2.music.126.net/aaa.jpg"
    assert out[0].extra["update_frequency"] == "每日更新"
    assert out[1].extra["description"] is None  # 空串归一为 None


def test_netease_list_charts_blocked(monkeypatch):
    # 反爬限流（code -462）视为接口错误抛 LookupError，由 REST 层转 502
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp({"code": -462, "list": []}))
    with pytest.raises(LookupError):
        charts.list_charts("netease")


QQ_DETAIL_RESP = {"code": 0, "subcode": 0, "date": "2026-09-23", "total_song_num": 2, "songlist": [
    {"data": {"songmid": "midAAA", "songname": "歌曲A", "interval": 200,
              "singer": [{"name": "歌手甲"}], "albumname": "专辑A", "albummid": "albAAA"}},
    {"data": {"songmid": "midBBB", "songname": "歌曲B", "interval": 180,
              "singer": [{"name": "歌手乙"}], "albumname": "专辑B", "albummid": "albBBB"}},
]}


class _FakeSong:
    """模拟 musicdl SongInfo：只带 with_valid_download_url 与 todict（normalize_song 用）。"""
    def __init__(self, mid, name, valid=True):
        self._d = {"identifier": mid, "song_name": name, "singers": ["歌手甲"],
                   "download_url": f"https://dl.example.com/{mid}.flac" if valid else "",
                   "ext": "flac", "raw_data": {"search": {}}}

    @property
    def with_valid_download_url(self):
        return bool(self._d["download_url"])

    def todict(self):
        return dict(self._d)


class _FakeQQClient:
    def __init__(self):
        self.default_cookies = None

    def _parsewiththirdpartapis(self, search_result):
        return _FakeSong("flac-" + search_result["songmid"], search_result["songname"], valid=False)

    def _parsewithofficialapiv1(self, search_result, song_info_flac=None,
                                lossless_quality_is_sufficient=True):
        return _FakeSong(search_result["songmid"], search_result["songname"], valid=True)


class _FakeMusicClient:
    def __init__(self):
        self.music_clients = {"QQMusicClient": _FakeQQClient()}


def test_qq_chart_tracks(monkeypatch):
    captured = {}

    def _fake_get(url, params=None, **kw):
        captured["params"] = params
        return FakeResp(QQ_DETAIL_RESP)

    monkeypatch.setattr(httpx, "get", _fake_get)
    monkeypatch.setattr(charts, "build_client", lambda sources: _FakeMusicClient())
    out = charts.get_chart_tracks("qq", "4", limit=2)
    assert captured["params"]["topid"] == "4"
    assert captured["params"]["song_num"] == 2  # limit 映射为 song_num
    assert captured["params"]["song_begin"] == 0
    assert [t.id for t in out] == ["QQMusicClient:midAAA", "QQMusicClient:midBBB"]
    assert out[0].title == "歌曲A"
    assert out[0].source == "QQMusicClient"
    assert out[0].ext == "flac"


def test_qq_chart_tracks_page_cap(monkeypatch):
    captured = {}
    monkeypatch.setattr(httpx, "get",
                        lambda url, params=None, **kw: captured.update(params=params) or FakeResp(QQ_DETAIL_RESP))
    monkeypatch.setattr(charts, "build_client", lambda sources: _FakeMusicClient())
    charts.get_chart_tracks("qq", "4")
    assert captured["params"]["song_num"] == 100  # limit 缺省取单页上限
    charts.get_chart_tracks("qq", "4", limit=150)
    assert captured["params"]["song_num"] == 100  # 超出按 100 截断


def test_qq_chart_tracks_skips_unresolvable(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(QQ_DETAIL_RESP))
    monkeypatch.setattr(charts, "build_client", lambda sources: _FakeMusicClient())
    monkeypatch.setattr(charts, "_resolve_qq_track",
                        lambda client, sr: _FakeSong(sr["songmid"], sr["songname"])
                        if sr["songmid"] == "midBBB" else None)
    out = charts.get_chart_tracks("qq", "4")
    assert [t.id for t in out] == ["QQMusicClient:midBBB"]  # 解析失败条目跳过，不阻断整榜


def test_qq_chart_tracks_error_code(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp({"code": -1}))
    with pytest.raises(LookupError):
        charts.get_chart_tracks("qq", "4")


def test_resolve_qq_track_falls_back_to_flac():
    class C:
        default_cookies = {"musickey": "x"}

        def _parsewiththirdpartapis(self, search_result):
            return _FakeSong("m1", "歌", valid=True)

        def _parsewithofficialapiv1(self, **kw):
            raise RuntimeError("boom")

    song = charts._resolve_qq_track(C(), {"songmid": "m1"})
    assert song is not None and song.with_valid_download_url  # official 异常回退 thirdpart 结果


def test_resolve_qq_track_none_when_no_url():
    class C:
        default_cookies = None

        def _parsewiththirdpartapis(self, search_result):
            return _FakeSong("m1", "歌", valid=False)

        def _parsewithofficialapiv1(self, **kw):
            return _FakeSong("m1", "歌", valid=False)

    assert charts._resolve_qq_track(C(), {"songmid": "m1"}) is None  # VIP/付费无地址 → None


def test_qq_chart_tracks_client_unavailable(monkeypatch):
    class _Empty:
        music_clients = {}

    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(QQ_DETAIL_RESP))
    monkeypatch.setattr(charts, "build_client", lambda sources: _Empty())
    with pytest.raises(LookupError):
        charts.get_chart_tracks("qq", "4")


def test_netease_chart_tracks(monkeypatch):
    calls = {}

    def _fake_parse(url, source=None):
        calls["url"] = url
        calls["source"] = source
        return [Track(id=f"NeteaseMusicClient:{i}", source="NeteaseMusicClient", title=f"歌{i}")
                for i in range(3)]

    monkeypatch.setattr(charts, "parse_playlist", _fake_parse)
    out = charts.get_chart_tracks("netease", "19723756", limit=2)
    assert calls["url"] == "https://music.163.com/playlist?id=19723756"  # 榜单 id 即 playlist id
    assert calls["source"] == "NeteaseMusicClient"
    assert len(out) == 2  # limit 返回后截断（网易侧全量解析，无法分页）


def test_netease_chart_tracks_no_limit(monkeypatch):
    monkeypatch.setattr(charts, "parse_playlist",
                        lambda url, source=None: [Track(id=f"NeteaseMusicClient:{i}",
                                                        source="NeteaseMusicClient", title=f"歌{i}")
                                                  for i in range(3)])
    assert len(charts.get_chart_tracks("netease", "19723756")) == 3
