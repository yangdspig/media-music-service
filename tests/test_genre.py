"""流派归一化与艺人流派兜底（iTunes get_artist_genre）单元测试：httpx 层 mock，离线运行。"""
import httpx
import pytest

from app import itunes
from app.genre import normalize_genre
# 注意：conftest 的 autouse fixture 会把 app.itunes.get_artist_genre 模块属性 stub 成 None 返回，
# 这里在导入期取真实函数引用（import 先于 fixture 执行），直接测真实实现
from app.itunes import get_artist_genre


class FakeResp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


def _artist_hit(name="林志颖", genre="Mandopop"):
    return {"results": [{"wrapperType": "artist", "artistName": name,
                         "primaryGenreName": genre}]}


@pytest.fixture(autouse=True)
def _clear_genre_cache():
    itunes._ARTIST_GENRE_CACHE.clear()
    yield
    itunes._ARTIST_GENRE_CACHE.clear()


@pytest.mark.parametrize("src,expected", [
    ("Mandopop", "国语流行"), ("國語流行樂", "国语流行"), ("国语流行乐", "国语流行"),
    ("Chinese Pop", "国语流行"),
    ("Cantopop", "粤语流行"), ("Cantopop/HK-Pop", "粤语流行"), ("HK-Pop", "粤语流行"),
    ("粵語流行", "粤语流行"),
    ("Pop", "流行"), ("流行樂", "流行"),
    ("摇滚", "摇滚"), ("Jazz", "Jazz"), ("", ""),  # 未映射原样返回（不做过度归一化）
])
def test_normalize_genre(src, expected):
    assert normalize_genre(src) == expected


def test_get_artist_genre_hit(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(_artist_hit()))
    assert get_artist_genre("林志颖") == "国语流行"  # t2s + normalize_genre


def test_get_artist_genre_storefront_chain(monkeypatch):
    """CN 无结果时按链回退 HK；country 参数顺序固定。"""
    calls = []

    def _fake_get(url, params=None, **kw):
        calls.append(params["country"])
        if params["country"] == "CN":
            return FakeResp({"results": []})
        return FakeResp(_artist_hit())

    monkeypatch.setattr(httpx, "get", _fake_get)
    assert get_artist_genre("林志颖") == "国语流行"
    assert calls == ["CN", "HK"]


def test_get_artist_genre_rejects_containment(monkeypatch):
    """实测教训：搜「阿杜」iTunes 可能只回「野狗阿杜」——包含关系虚高分不得采纳。"""
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: FakeResp(_artist_hit(name="野狗阿杜")))
    assert get_artist_genre("阿杜") is None


def test_get_artist_genre_exact_preferred_over_fuzzy(monkeypatch):
    """同一结果集里归一化相等者优先于模糊命中（避免排序靠前的无关艺人抢先）。"""
    resp = {"results": [
        {"wrapperType": "artist", "artistName": "野狗阿杜", "primaryGenreName": "Rock"},
        {"wrapperType": "artist", "artistName": "阿杜", "primaryGenreName": "Mandopop"},
    ]}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp(resp))
    assert get_artist_genre("阿杜") == "国语流行"


def test_get_artist_genre_429_backoff(monkeypatch):
    """429 退避重试最多 2 次（25s 递增），第三次成功则正常返回。"""
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    state = {"n": 0}

    def _fake_get(*a, **kw):
        state["n"] += 1
        if state["n"] <= 2:
            return FakeResp({}, status_code=429)
        return FakeResp(_artist_hit(genre="国语流行乐"))

    monkeypatch.setattr(httpx, "get", _fake_get)
    assert get_artist_genre("林志颖") == "国语流行"
    assert sleeps == [25.0, 50.0]


def test_get_artist_genre_429_exhausted_returns_none(monkeypatch):
    """429 重试耗尽：异常被吞掉返回 None（不影响归档主流程），且缓存该负结果。"""
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: FakeResp({}, status_code=429))
    assert get_artist_genre("林志颖") is None
    assert "林志颖" in itunes._ARTIST_GENRE_CACHE


def test_get_artist_genre_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "get",
                        lambda *a, **kw: calls.append(1) or FakeResp(_artist_hit(name="阿桑", genre="Pop")))
    assert get_artist_genre("阿桑") == "流行"
    assert get_artist_genre("阿桑") == "流行"
    assert len(calls) == 1  # 第二次走进程内缓存，不再请求


def test_get_artist_genre_network_error_returns_none(monkeypatch):
    def _boom(*a, **kw):
        raise httpx.ConnectError("network down")
    monkeypatch.setattr(httpx, "get", _boom)
    assert get_artist_genre("周杰伦") is None
