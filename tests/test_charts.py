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
