"""中文专辑元数据（网易云/QQ）客户端与编排层单元测试：httpx 层 mock，离线运行。"""
import httpx
import pytest

from app import meta, netease_meta, qq_meta
from app.schemas import AlbumInfo, AlbumSummary, AlbumTrack


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


def _summary(cid: str, title: str, artists=None, **kw) -> AlbumSummary:
    return AlbumSummary(collection_id=cid, title=title, artists=artists or [], **kw)


def _itunes_album(**over) -> AlbumInfo:
    base = dict(collection_id="12345", title="Ye Hui Mei", artists=["Jay Chou"],
                release_date="2003-07-31T07:00:00Z", track_count=1, storefront="US",
                tracks=[AlbumTrack(disc=1, track=1, title="Yi Fu Zhi Ming",
                                   artists=["Jay Chou"], duration_s=342.0)])
    base.update(over)
    return AlbumInfo(**base)


def test_search_fallback_itunes_empty(monkeypatch):
    monkeypatch.setattr(meta.itunes, "search_albums", lambda **kw: [])
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: [_summary("netease:18905", "叶惠美", ["周杰伦"], meta_source="netease")])
    out = meta.search_albums("叶惠美")
    assert [a.collection_id for a in out] == ["netease:18905"]


def test_search_appends_cn_when_results_lack_cjk(monkeypatch):
    monkeypatch.setattr(meta.itunes, "search_albums",
                        lambda **kw: [_summary("100", "Fantasy")])
    monkeypatch.setattr(meta.qq_meta, "search_albums",
                        lambda *a, **kw: [_summary("qq:mid1", "范特西", ["周杰伦"], meta_source="qq")])
    calls = []
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: calls.append(1) or [])
    out = meta.search_albums("范特西", artist="周杰伦", limit=10)
    assert [a.collection_id for a in out] == ["100", "qq:mid1"]
    assert calls == [1]  # 先查网易云，为空再查 QQ


def test_search_no_cn_when_cjk_covered(monkeypatch):
    monkeypatch.setattr(meta.itunes, "search_albums",
                        lambda **kw: [_summary("100", "范特西", ["周杰伦"])])
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不应调用中文源")))
    out = meta.search_albums("范特西")
    assert [a.collection_id for a in out] == ["100"]


def test_get_album_prefix_routing(monkeypatch):
    marker = _itunes_album(collection_id="qq:x", meta_source="qq")
    monkeypatch.setattr(meta.qq_meta, "get_album", lambda mid: marker)
    assert meta.get_album("qq:x") is marker


def test_get_album_merges_cn_supplement(monkeypatch):
    monkeypatch.setattr(meta.itunes, "get_album", lambda cid: _itunes_album())
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: [_summary("netease:18905", "叶惠美", ["周杰伦"],
                                                   release_date="2003-07-31", track_count=1)])
    monkeypatch.setattr(meta.netease_meta, "get_album",
                        lambda aid: _itunes_album(collection_id="netease:18905", title="叶惠美",
                                                  artists=["周杰伦"], description="中文简介",
                                                  meta_source="netease"))
    info = meta.get_album("12345")
    # 罗马音标题/艺人被中文名替换，简介合并，来源标记复合值；曲目表保持 iTunes 的
    assert info.title == "叶惠美"
    assert info.artists == ["周杰伦"]
    assert info.description == "中文简介"
    assert info.meta_source == "itunes+netease"
    assert info.tracks[0].title == "Yi Fu Zhi Ming"


def test_get_album_merge_rejects_low_similarity(monkeypatch):
    monkeypatch.setattr(meta.itunes, "get_album",
                        lambda cid: _itunes_album(title="叶惠美", artists=["周杰伦"]))
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: [_summary("netease:999", "完全无关的专辑", ["张三"])])
    monkeypatch.setattr(meta.qq_meta, "search_albums", lambda *a, **kw: [])
    info = meta.get_album("12345")
    assert info.title == "叶惠美"
    assert info.description is None
    assert info.meta_source == "itunes"


def test_get_album_romanized_fallback_by_date_and_count(monkeypatch):
    # 罗马音标题与中文名相似度为 0：放宽为发行日期+曲目数精确一致即接受
    monkeypatch.setattr(meta.itunes, "get_album", lambda cid: _itunes_album())
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: [_summary("netease:18905", "叶惠美", ["周杰伦"],
                                                   release_date="2003-07-31", track_count=1)])
    monkeypatch.setattr(meta.netease_meta, "get_album",
                        lambda aid: _itunes_album(collection_id="netease:18905", title="叶惠美",
                                                  artists=["周杰伦"], description="中文简介",
                                                  meta_source="netease"))
    info = meta.get_album("12345")
    assert info.description == "中文简介"
    assert info.meta_source == "itunes+netease"


def test_get_album_takeover_when_itunes_has_no_tracks(monkeypatch):
    def _raise(cid):
        raise LookupError("no tracks")
    monkeypatch.setattr(meta.itunes, "get_album", _raise)
    monkeypatch.setattr(meta, "_itunes_summary",
                        lambda cid: _summary("12345", "Ye Hui Mei", ["Jay Chou"],
                                             release_date="2003-07-31T07:00:00Z", track_count=1))
    cn_album = _itunes_album(collection_id="netease:18905", title="叶惠美", artists=["周杰伦"],
                             meta_source="netease")
    monkeypatch.setattr(meta.netease_meta, "search_albums",
                        lambda *a, **kw: [_summary("netease:18905", "叶惠美", ["周杰伦"],
                                                   release_date="2003-07-31", track_count=1)])
    monkeypatch.setattr(meta.netease_meta, "get_album", lambda aid: cn_album)
    info = meta.get_album("12345")
    assert info is cn_album
    assert info.meta_source == "netease"


def test_get_album_takeover_all_fail_keeps_lookup_error(monkeypatch):
    def _raise(cid):
        raise LookupError("no tracks")
    monkeypatch.setattr(meta.itunes, "get_album", _raise)
    monkeypatch.setattr(meta, "_itunes_summary", lambda cid: None)
    with pytest.raises(LookupError):
        meta.get_album("12345")


def test_cn_failure_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(meta.itunes, "get_album", lambda cid: _itunes_album(title="范特西", artists=["周杰伦"]))

    def _boom(*a, **kw):
        raise httpx.ConnectError("network down")
    monkeypatch.setattr(meta.netease_meta, "search_albums", _boom)
    monkeypatch.setattr(meta.qq_meta, "search_albums", _boom)
    info = meta.get_album("12345")  # 中文源全部失败不抛错
    assert info.meta_source == "itunes"


def test_album_dict_keeps_real_meta_source():
    # album.py 生成 manifest 时不得再把 meta_source 硬编码为 "itunes"
    import inspect
    from app import album as album_mod
    src = inspect.getsource(album_mod._run_album)
    assert '"meta_source": "itunes"' not in src
    assert 'album.model_dump(exclude={"tracks"})' in src


def test_write_album_info_with_description(tmp_path):
    from app.archive import _write_album_info
    album = {"title": "叶惠美", "artists": ["周杰伦"], "release_date": "2003-07-31",
             "genre": "Pop", "meta_source": "itunes+netease", "collection_id": "12345",
             "storefront": "CN", "description": "专辑简介文本"}
    entries = [{"disc": 1, "track": 1, "title": "以父之名", "duration_s": 342.0}]
    _write_album_info(tmp_path, album, entries, "叶惠美", "周杰伦")
    text = (tmp_path / "album_info.txt").read_text(encoding="utf-8")
    assert "简介：" in text
    assert "专辑简介文本" in text
    assert "简介暂缺" not in text
    assert "元数据来源：itunes+netease" in text


def test_write_album_info_cn_source_no_itunes_annotation(tmp_path):
    from app.archive import _write_album_info
    # 纯中文源专辑：不出现「iTunes 原名」标注与 storefront；无简介时不写占位行
    album = {"title": "范特西", "artists": ["周杰伦"], "release_date": "2001-09-14",
             "genre": None, "meta_source": "qq", "collection_id": "qq:000I5jJB3blWeN"}
    entries = [{"disc": 1, "track": 1, "title": "爱在西元前", "duration_s": None}]
    _write_album_info(tmp_path, album, entries, "范特西", "周杰伦")
    text = (tmp_path / "album_info.txt").read_text(encoding="utf-8")
    assert "iTunes 原名" not in text
    assert "storefront" not in text
    assert "简介暂缺" not in text
    assert "元数据来源：qq" in text
