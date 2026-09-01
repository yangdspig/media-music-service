"""中文专辑元数据（网易云/QQ）客户端与编排层单元测试：httpx 层 mock，离线运行。"""
import httpx
import pytest

from app import netease_meta
from app.schemas import AlbumInfo


class FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


NE_SEARCH_RESP = {"result": {"albums": [
    {"id": 18905, "name": "叶惠美", "publishTime": 1059580800000, "size": 11,
     "picUrl": "https://p2.music.126.net/xxx.jpg", "artist": {"name": "周杰伦"},
     "description": ""},
]}}

NE_DETAIL_RESP = {
    "code": 200, "resourceState": True,
    "album": {"id": 18905, "name": "叶惠美", "publishTime": 1059580800000,
              "description": "2003年最被期待的专辑", "picUrl": "https://p2.music.126.net/xxx.jpg",
              "artist": {"name": "周杰伦"}, "company": ""},
    "songs": [
        {"no": 2, "cd": "1", "name": "懦夫", "dt": 218000, "ar": [{"name": "周杰伦"}, {"name": "余妮"}]},
        {"no": 1, "cd": "1", "name": "以父之名", "dt": 342000, "ar": [{"name": "周杰伦"}]},
    ],
}


def test_netease_search(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResp(NE_SEARCH_RESP))
    out = netease_meta.search_albums("叶惠美", artist="周杰伦")
    assert len(out) == 1
    a = out[0]
    assert a.collection_id == "netease:18905"
    assert a.title == "叶惠美"
    assert a.artists == ["周杰伦"]
    assert a.release_date == "2003-07-31"  # publishTime 为东八区零点，须按 UTC+8 转日期
    assert a.track_count == 11
    assert a.cover_url == "https://p2.music.126.net/xxx.jpg"
    assert a.meta_source == "netease"
    assert a.description is None  # 空串归一为 None


def test_netease_get_album(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(NE_DETAIL_RESP))
    info = netease_meta.get_album("18905")
    assert isinstance(info, AlbumInfo)
    assert info.collection_id == "netease:18905"
    assert info.title == "叶惠美"
    assert info.description == "2003年最被期待的专辑"
    assert info.meta_source == "netease"
    assert info.track_count == 2
    # 曲目按 no 排序，时长 dt(ms) → duration_s，多艺人保留
    assert [(t.track, t.title, t.disc, t.duration_s, t.artists) for t in info.tracks] == [
        (1, "以父之名", 1, 342.0, ["周杰伦"]),
        (2, "懦夫", 1, 218.0, ["周杰伦", "余妮"]),
    ]


def test_netease_get_album_blocked(monkeypatch):
    # 反爬限流（code -462）视为未命中
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp({"code": -462, "data": {}, "message": "blocked"}))
    with pytest.raises(LookupError):
        netease_meta.get_album("18905")
