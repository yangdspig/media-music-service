"""中文专辑元数据（网易云/QQ）客户端与编排层单元测试：httpx 层 mock，离线运行。"""
import httpx
import pytest

from app import netease_meta, qq_meta
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


QQ_SEARCH_RESP = {"code": 0, "data": {"album": {"list": [
    {"albumID": 8217, "albumMID": "000I5jJB3blWeN", "albumName": "范特西",
     "singerName": "周杰伦", "publicTime": "2001-09-14", "song_count": 10},
]}}}

QQ_DETAIL_RESP = {"code": 0, "req_1": {"code": 0, "data": {
    "basicInfo": {"albumMid": "000I5jJB3blWeN", "albumName": "范特西",
                  "publishDate": "2001-09-14", "desc": "周杰伦第二张专辑"},
    "singer": {"singerList": [{"name": "周杰伦"}]},
    "company": {"name": "杰威尔音乐有限公司"},
}}}

QQ_SONGLIST_RESP = {"code": 0, "req_1": {"code": 0, "data": {
    "totalNum": 2,
    "songList": [
        {"songInfo": {"title": "简单爱", "interval": 270, "index_album": 2, "singer": [{"name": "周杰伦"}]}},
        {"songInfo": {"title": "爱在西元前", "interval": 234, "index_album": 1, "singer": [{"name": "周杰伦"}]}},
    ],
}}}


def _qq_fake_post(url, json=None, **kw):
    module = (json or {}).get("req_1", {}).get("module", "")
    if module.endswith("AlbumInfoServer"):
        return FakeResp(QQ_DETAIL_RESP)
    return FakeResp(QQ_SONGLIST_RESP)


def test_qq_search(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(QQ_SEARCH_RESP))
    out = qq_meta.search_albums("范特西")
    assert len(out) == 1
    a = out[0]
    assert a.collection_id == "qq:000I5jJB3blWeN"
    assert a.title == "范特西"
    assert a.artists == ["周杰伦"]
    assert a.release_date == "2001-09-14"
    assert a.track_count == 10
    assert a.cover_url == "https://y.gtimg.cn/music/photo_new/T002R800x800M000000I5jJB3blWeN.jpg"
    assert a.meta_source == "qq"


def test_qq_get_album(monkeypatch):
    monkeypatch.setattr(httpx, "post", _qq_fake_post)
    info = qq_meta.get_album("000I5jJB3blWeN")
    assert info.collection_id == "qq:000I5jJB3blWeN"
    assert info.title == "范特西"
    assert info.artists == ["周杰伦"]
    assert info.release_date == "2001-09-14"
    assert info.description == "周杰伦第二张专辑"
    assert info.meta_source == "qq"
    # 曲目按 index_album 排序，interval 为秒
    assert [(t.track, t.title, t.disc, t.duration_s) for t in info.tracks] == [
        (1, "爱在西元前", 1, 234.0),
        (2, "简单爱", 1, 270.0),
    ]


def test_qq_get_album_not_found(monkeypatch):
    empty = {"code": 0, "req_1": {"code": 0, "data": {"basicInfo": {}}}}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: FakeResp(empty))
    with pytest.raises(LookupError):
        qq_meta.get_album("00000000000000")
