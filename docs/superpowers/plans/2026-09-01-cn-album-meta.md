# 中文专辑元数据补充（网易云/QQ）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** iTunes 专辑元数据覆盖不足时，用网易云/QQ 公开网页接口回退补齐专辑详情（含曲目表），并为所有专辑补充中文简介与中文显示名。

**Architecture:** 新增 `app/meta.py` 编排层（iTunes 首选，网易云→QQ 固定链回退/补充），`app/netease_meta.py` 与 `app/qq_meta.py` 两个独立客户端；`collection_id` 用 `netease:`/`qq:` 前缀路由（无前缀=iTunes，向后兼容）。REST/MCP 接口签名不变。

**Tech Stack:** Python 3.11+、FastAPI、httpx（既有依赖，不新增任何第三方库）、pytest（`.venv/bin/python -m pytest`）。

**Spec:** `docs/superpowers/specs/2026-09-01-cn-album-meta-design.md`（接口已实测验证，fixture 取自真实响应结构）。

## Global Constraints

- 不新增第三方依赖；只用 httpx（项目已有）。
- 中文源固定回退顺序：网易云 → QQ；不做配置项。
- 中文源一切失败（网络/限流/解析）仅 `log.warning` 降级，不得改变主链路行为；只有所有来源都失败才维持现有 404/502 语义。
- 各中文源单请求超时 10s（`httpx.Timeout(10.0)`）。
- 简介匹配门槛：候选标题与艺人归一化相似度（`album._sim`）均 ≥0.6；罗马音场景（iTunes 标题无 CJK）放宽为发行日期前 10 位 + 曲目数精确一致。
- 显示名替换 CJK 保护：仅当原值不含 CJK 而中文源值含 CJK 时才替换（复用 `album._CJK_RE`）。
- 代码注释用中文，风格对齐现有 `app/itunes.py`（模块 docstring 说明设计要点 + 实测结论）。
- 测试全部离线：monkeypatch httpx 层，不发真实请求。
- 每任务结束单独 commit，commit message 用中文 conventional 格式（对齐 `git log` 现有风格，如 `feat: ...`）。
- 运行测试命令统一为 `.venv/bin/python -m pytest tests/ -v`（仓库根目录下执行）。

---

### Task 1: schemas 扩展 + 网易云客户端 `app/netease_meta.py`

**Files:**
- Modify: `app/schemas.py:61-69`（`AlbumSummary` 加两字段）
- Create: `app/netease_meta.py`
- Test: `tests/test_cn_meta.py`（新建）

**Interfaces:**
- Consumes: 现有 `AlbumSummary`/`AlbumInfo`/`AlbumTrack`（`app/schemas.py`）。
- Produces:
  - `AlbumSummary.description: Optional[str] = None`、`AlbumSummary.meta_source: str = "itunes"`（`AlbumInfo` 自动继承）
  - `netease_meta.search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]`（`collection_id` 形如 `netease:18905`）
  - `netease_meta.get_album(album_id: str) -> AlbumInfo`（未命中/限流抛 `LookupError`，网络错误抛 httpx 异常）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_cn_meta.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.netease_meta'`）

- [ ] **Step 3: 扩展 schemas**

`app/schemas.py` 的 `AlbumSummary`（61-69 行），docstring 改为「专辑摘要（iTunes / 网易云 / QQ 搜索结果项）」，并在 `genre` 字段后追加两字段：

```python
class AlbumSummary(BaseModel):
    """专辑摘要（iTunes / 网易云 / QQ 搜索结果项）。"""
    collection_id: str = Field(description="专辑 id：iTunes collectionId，或带前缀的中文源 id（netease:xxx / qq:xxx）")
    title: str = Field(description="专辑名")
    artists: list[str] = Field(default_factory=list, description="艺人列表")
    release_date: Optional[str] = Field(default=None, description="发行日期（ISO）")
    track_count: int = Field(default=0, description="曲目数")
    cover_url: Optional[str] = Field(default=None, description="高清封面 URL")
    genre: Optional[str] = Field(default=None, description="流派")
    description: Optional[str] = Field(default=None, description="专辑简介（网易云/QQ 提供，可能为空）")
    meta_source: str = Field(default="itunes", description="元数据来源：itunes / netease / qq / itunes+netease / itunes+qq")
```

- [ ] **Step 4: 实现 `app/netease_meta.py`**

```python
"""网易云专辑元数据客户端：公开网页 API（免登录）。

实测要点（2026-09-01）：
- 搜索 POST /api/search/get（type=10 专辑），PC UA + Referer 即可；
- 详情 GET /api/v1/album/{id}，需移动端 UA + os/appver cookie 才返回完整数据：
  专辑简介在 album.description，曲目在顶层 songs（no=序号、cd=碟号字符串、dt=毫秒时长、ar=艺人）；
- publishTime 为东八区零点的毫秒时间戳，须按 UTC+8 转日期（用 UTC 会差一天）；
- 存在反爬限流（code -462），本模块将其视为未命中抛 LookupError，调用方负责降级。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .schemas import AlbumInfo, AlbumSummary, AlbumTrack

SEARCH_URL = "https://music.163.com/api/search/get"
ALBUM_URL = "https://music.163.com/api/v1/album/{id}"

_SEARCH_HEADERS = {
    "Referer": "https://music.163.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}
_DETAIL_HEADERS = {
    "Referer": "https://music.163.com/",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
    "Cookie": "os=ios; appver=8.20.21",
}

_TIMEOUT = httpx.Timeout(10.0)
_CST = timezone(timedelta(hours=8))  # publishTime 为东八区零点


def _ms_to_date(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=_CST).date().isoformat()


def _to_summary(a: dict, fallback_id: str | None = None) -> AlbumSummary:
    artist = a.get("artist") or {}
    album_id = a.get("id") or fallback_id
    return AlbumSummary(
        collection_id=f"netease:{album_id}",
        title=a.get("name") or "未知专辑",
        artists=[artist["name"]] if artist.get("name") else [],
        release_date=_ms_to_date(a.get("publishTime")),
        track_count=a.get("size") or 0,
        cover_url=a.get("picUrl"),
        description=(a.get("description") or "").strip() or None,
        meta_source="netease",
    )


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """按专辑名（可叠加艺人）搜索专辑。"""
    term = f"{artist} {keyword}".strip() if artist else keyword
    r = httpx.post(SEARCH_URL, data={"s": term, "type": 10, "limit": limit},
                   headers=_SEARCH_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    albums = (r.json().get("result") or {}).get("albums") or []
    return [_to_summary(a) for a in albums if a.get("id")]


def get_album(album_id: str) -> AlbumInfo:
    """取专辑详情与曲目表；未命中/限流时抛 LookupError，网络错误向上抛 httpx 异常。"""
    r = httpx.get(ALBUM_URL.format(id=album_id), headers=_DETAIL_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    a = data.get("album") or {}
    if not a.get("name"):
        raise LookupError(f"网易云未找到专辑（id={album_id}, code={data.get('code')}）")
    summary = _to_summary(a, fallback_id=album_id)
    songs = data.get("songs") or []
    tracks = sorted(
        (
            AlbumTrack(
                disc=int(s.get("cd") or 1),
                track=s.get("no") or i + 1,
                title=s.get("name") or "未知",
                artists=[x["name"] for x in s.get("ar", []) if x.get("name")],
                duration_s=round(s["dt"] / 1000, 1) if s.get("dt") else None,
            )
            for i, s in enumerate(songs)
        ),
        key=lambda t: (t.disc, t.track),
    )
    if tracks:
        summary.track_count = len(tracks)
    return AlbumInfo(**summary.model_dump(), tracks=tracks)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add app/schemas.py app/netease_meta.py tests/test_cn_meta.py
git commit -m "feat: 网易云专辑元数据客户端 + AlbumSummary 简介/来源字段"
```

---

### Task 2: QQ 音乐客户端 `app/qq_meta.py`

**Files:**
- Create: `app/qq_meta.py`
- Test: `tests/test_cn_meta.py`（追加）

**Interfaces:**
- Consumes: Task 1 扩展后的 `AlbumSummary`/`AlbumInfo`/`AlbumTrack`。
- Produces:
  - `qq_meta.search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]`（`collection_id` 形如 `qq:000I5jJB3blWeN`，封面按 albumMid 拼 `T002R800x800M000`）
  - `qq_meta.get_album(album_mid: str) -> AlbumInfo`（未找到/接口错误抛 `LookupError`）

- [ ] **Step 1: 追加失败测试**

`tests/test_cn_meta.py` 追加（import 处加 `from app import qq_meta`）：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.qq_meta'`）

- [ ] **Step 3: 实现 `app/qq_meta.py`**

```python
"""QQ 音乐专辑元数据客户端：公开网页接口（免登录）。

实测要点（2026-09-01）：
- 搜索 GET c.y.qq.com/soso/fcgi-bin/client_search_cp（t=8 专辑），需 Referer: y.qq.com；
- 详情走 u.y.qq.com/cgi-bin/musicu.fcg 网关（POST JSON，req_1.module/method/param）：
  GetAlbumDetail 取 basicInfo（albumName/publishDate/desc）与 singer.singerList（艺人），
  GetAlbumSongList 取曲目表（songInfo.title / interval 秒 / index_album 序号 / belongCD 碟号可空），
  albumID 传 0 即可（实测可用）；
- 封面按 albumMid 拼 T002R800x800M000 高清 URL。
"""
from __future__ import annotations

import httpx

from .schemas import AlbumInfo, AlbumSummary, AlbumTrack

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"

_HEADERS = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0"}
_TIMEOUT = httpx.Timeout(10.0)


def _cover_url(album_mid: str | None) -> str | None:
    return f"https://y.gtimg.cn/music/photo_new/T002R800x800M000{album_mid}.jpg" if album_mid else None


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """按专辑名（可叠加艺人）搜索专辑。"""
    term = f"{artist} {keyword}".strip() if artist else keyword
    r = httpx.get(SEARCH_URL, params={"t": 8, "w": term, "format": "json", "n": limit},
                  headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    items = ((r.json().get("data") or {}).get("album") or {}).get("list") or []
    out = []
    for a in items:
        mid = a.get("albumMID")
        if not mid:
            continue
        out.append(AlbumSummary(
            collection_id=f"qq:{mid}",
            title=a.get("albumName") or "未知专辑",
            artists=[a["singerName"]] if a.get("singerName") else [],
            release_date=a.get("publicTime"),
            track_count=a.get("song_count") or 0,
            cover_url=_cover_url(mid),
            meta_source="qq",
        ))
    return out


def _musicu(module: str, method: str, param: dict) -> dict:
    """调 musicu.fcg 网关，返回 req_1.data；接口错误抛 LookupError。"""
    payload = {"comm": {"ct": 24, "cv": 0},
               "req_1": {"module": module, "method": method, "param": param}}
    r = httpx.post(MUSICU_URL, json=payload, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    req = r.json().get("req_1") or {}
    if req.get("code") != 0:
        raise LookupError(f"QQ 接口返回错误（{method}, code={req.get('code')}）")
    return req.get("data") or {}


def get_album(album_mid: str) -> AlbumInfo:
    """取专辑详情与曲目表；未找到/接口错误抛 LookupError，网络错误向上抛 httpx 异常。"""
    detail = _musicu("music.musichallAlbum.AlbumInfoServer", "GetAlbumDetail", {"albumMid": album_mid})
    bi = detail.get("basicInfo") or {}
    if not bi.get("albumName"):
        raise LookupError(f"QQ 未找到专辑（albumMid={album_mid}）")
    song_data = _musicu("music.musichallAlbum.AlbumSongList", "GetAlbumSongList",
                        {"albumMid": album_mid, "albumID": 0, "begin": 0, "num": 100, "order": 2})
    tracks = sorted(
        (
            AlbumTrack(
                disc=int(s.get("belongCD") or 1),
                track=s.get("index_album") or i + 1,
                title=s.get("title") or s.get("name") or "未知",
                artists=[x["name"] for x in s.get("singer", []) if x.get("name")],
                duration_s=float(s["interval"]) if s.get("interval") else None,
            )
            for i, item in enumerate(song_data.get("songList") or [])
            for s in [item.get("songInfo") or item]
        ),
        key=lambda t: (t.disc, t.track),
    )
    artists = [x["name"] for x in (detail.get("singer") or {}).get("singerList", []) if x.get("name")]
    return AlbumInfo(
        collection_id=f"qq:{album_mid}",
        title=bi.get("albumName") or "未知专辑",
        artists=artists,
        release_date=bi.get("publishDate"),
        track_count=len(tracks),
        cover_url=_cover_url(album_mid),
        description=(bi.get("desc") or "").strip() or None,
        meta_source="qq",
        tracks=tracks,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/qq_meta.py tests/test_cn_meta.py
git commit -m "feat: QQ 音乐专辑元数据客户端（musicu.fcg 网关）"
```

---

### Task 3: 编排层 `app/meta.py`

**Files:**
- Create: `app/meta.py`
- Test: `tests/test_cn_meta.py`（追加）

**Interfaces:**
- Consumes: `itunes.search_albums`/`itunes.get_album`/`itunes.LOOKUP_URL`/`itunes._to_summary`；`netease_meta`、`qq_meta` 的 `search_albums`/`get_album`（Task 1/2）；`album._CJK_RE`、`album._sim`（包内复用，无循环依赖：`album.py` 不 import `meta.py`）。
- Produces（Task 4 的 `main.py` 依赖这两个函数替换对 `itunes` 的直接调用）：
  - `meta.search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]`
  - `meta.get_album(collection_id: str) -> AlbumInfo`（前缀路由；iTunes id 走接管/补充；全失败抛 `LookupError` 或 httpx 异常，语义与 `itunes.get_album` 一致）

- [ ] **Step 1: 追加失败测试**

`tests/test_cn_meta.py` 追加（import 处加 `from app import meta`、`from app.schemas import AlbumSummary, AlbumTrack`）：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.meta'`）

- [ ] **Step 3: 实现 `app/meta.py`**

```python
"""专辑元数据编排层：iTunes 首选，网易云/QQ 回退与简介补充。

规则（对应 docs/superpowers/specs/2026-09-01-cn-album-meta-design.md）：
- collection_id 命名空间：无前缀=iTunes，netease:/qq: 前缀路由到对应中文源；
- search_albums：iTunes 无结果，或关键词含 CJK 而结果全不含 CJK（覆盖不足）时，
  依次回退网易云→QQ 补齐至 limit（首个非空来源即停）；
- get_album（iTunes id）：各 storefront 均无曲目时用「专辑名+艺人」在中文源找同专辑
  整体接管（含曲目表）；iTunes 命中曲目表时 best-effort 合并中文源的简介与中文显示名；
- 同专辑判定：标题与艺人归一化相似度均 ≥0.6；罗马音场景（标题无 CJK，相似度天然低）
  放宽为发行日期前 10 位 + 曲目数精确一致（中文源搜索已带艺人关键词收敛结果集）；
- 中文源一切失败仅记 warning 降级，主链路行为与纯 iTunes 时一致。
"""
from __future__ import annotations

import logging

import httpx

from . import itunes, netease_meta, qq_meta
from .album import _CJK_RE, _sim
from .schemas import AlbumInfo, AlbumSummary

log = logging.getLogger(__name__)

_CN_CLIENTS = (netease_meta, qq_meta)
_SIM_THRESHOLD = 0.6
_LOOKUP_TIMEOUT = httpx.Timeout(10.0)


def _split_id(collection_id: str) -> tuple[str | None, str]:
    """拆分带前缀的 collection_id；无前缀返回 (None, 原 id)。"""
    prefix, sep, rest = collection_id.partition(":")
    if sep and prefix in ("netease", "qq") and rest:
        return prefix, rest
    return None, collection_id


def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    """专辑搜索：iTunes 优先，覆盖不足时中文源补齐（iTunes 网络异常向上抛，与现状一致）。"""
    results = itunes.search_albums(keyword=keyword, artist=artist, limit=limit)
    cjk_query = bool(_CJK_RE.search(keyword or "") or _CJK_RE.search(artist or ""))
    need_cn = not results or (cjk_query and not any(_CJK_RE.search(r.title) for r in results))
    if need_cn and len(results) < limit:
        for client in _CN_CLIENTS:
            try:
                cn = client.search_albums(keyword, artist=artist, limit=limit - len(results))
            except Exception as e:
                log.warning("%s 专辑搜索失败（降级跳过）: %s", client.__name__, e)
                continue
            if cn:
                results = results + cn
                break
    return results


def get_album(collection_id: str) -> AlbumInfo:
    """专辑详情：按 id 前缀路由；iTunes id 走接管/补充逻辑。"""
    prefix, raw_id = _split_id(collection_id)
    if prefix == "netease":
        return netease_meta.get_album(raw_id)
    if prefix == "qq":
        return qq_meta.get_album(raw_id)
    try:
        album = itunes.get_album(raw_id)
    except LookupError:
        album = _cn_takeover(raw_id)
        if album is None:
            raise
        return album
    return _merge_cn_supplement(album)


def _itunes_summary(collection_id: str) -> AlbumSummary | None:
    """轻量 lookup 取 iTunes 专辑摘要（接管时用于拼中文源搜索关键词）。"""
    r = httpx.get(itunes.LOOKUP_URL, params={"id": collection_id, "country": "CN"},
                  timeout=_LOOKUP_TIMEOUT)
    r.raise_for_status()
    coll = next((i for i in r.json().get("results", [])
                 if i.get("wrapperType") == "collection"), None)
    return itunes._to_summary(coll) if coll else None


def _find_same_album(client, title: str, artist: str | None,
                     release_date: str | None = None, track_count: int = 0) -> AlbumInfo | None:
    """在中文源找同专辑并取详情；未找到/失败返回 None（降级）。"""
    try:
        cands = client.search_albums(title, artist=artist, limit=5)
    except Exception as e:
        log.warning("%s 专辑搜索失败（降级跳过）: %s", client.__name__, e)
        return None
    best, best_score = None, 0.0
    for c in cands:
        ts = _sim(title, c.title)
        asim = max((_sim(artist, a) for a in c.artists), default=0.0) if artist else 0.5
        score = 0.6 * ts + 0.4 * asim
        if ts >= _SIM_THRESHOLD and asim >= _SIM_THRESHOLD and score > best_score:
            best, best_score = c, score
    if best is None and not _CJK_RE.search(title or "") and release_date and track_count:
        # 罗马音场景：标题相似度天然为 0，放宽为发行日期+曲目数精确一致
        best = next((c for c in cands
                     if (c.release_date or "")[:10] == release_date[:10]
                     and c.track_count == track_count), None)
    if best is None:
        return None
    _, raw_id = _split_id(best.collection_id)
    try:
        return client.get_album(raw_id)
    except Exception as e:
        log.warning("%s 专辑详情获取失败（降级跳过）: %s", client.__name__, e)
        return None


def _cn_takeover(collection_id: str) -> AlbumInfo | None:
    """iTunes 各 storefront 无曲目时：中文源整体接管（含曲目表）。"""
    summary = _itunes_summary(collection_id)
    if summary is None:
        return None
    artist = summary.artists[0] if summary.artists else None
    for client in _CN_CLIENTS:
        album = _find_same_album(client, summary.title, artist,
                                 release_date=summary.release_date,
                                 track_count=summary.track_count)
        if album is not None and album.tracks:
            return album
    return None


def _merge_cn_supplement(album: AlbumInfo) -> AlbumInfo:
    """iTunes 命中时合并中文源简介与中文显示名（CJK 保护，命中一个中文源即停）。"""
    artist = album.artists[0] if album.artists else None
    for client in _CN_CLIENTS:
        cn = _find_same_album(client, album.title, artist,
                              release_date=album.release_date,
                              track_count=album.track_count)
        if cn is None:
            continue
        changed = False
        if cn.description and not album.description:
            album.description = cn.description
            changed = True
        if not _CJK_RE.search(album.title) and _CJK_RE.search(cn.title):
            album.title = cn.title
            changed = True
        if (album.artists and cn.artists
                and not _CJK_RE.search(album.artists[0]) and _CJK_RE.search(cn.artists[0])):
            album.artists = cn.artists
            changed = True
        if changed:
            album.meta_source = f"itunes+{cn.meta_source}"
        return album
    return album
```

注意：`test_get_album_merges_cn_supplement` 中 iTunes 专辑标题为罗马音 "Ye Hui Mei"，与 "叶惠美" 的 `_sim` 为 0，走的是「发行日期+曲目数」放宽路径（fixture 里 `release_date="2003-07-31"`、`track_count=1` 与 iTunes 侧一致）；`test_get_album_romanized_fallback_by_date_and_count` 与其互为印证，保留两个用例防止门槛逻辑被改坏。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add app/meta.py tests/test_cn_meta.py
git commit -m "feat: 专辑元数据编排层（iTunes 首选，网易云/QQ 回退接管与简介合并）"
```

---

### Task 4: 接线 main.py / album.py / mcp_adapter.py

**Files:**
- Modify: `app/main.py:14, 66-72, 81-86`
- Modify: `app/album.py:401`
- Modify: `mcp_adapter.py:167-198, 202-218`（仅 docstring）
- Test: `tests/test_cn_meta.py`（追加一条回归）

**Interfaces:**
- Consumes: `meta.search_albums`、`meta.get_album`（Task 3）。
- Produces: REST `/api/v1/albums/search`、`/api/v1/albums/{collection_id}`、`/albums/{collection_id}/download` 对外行为变为编排层语义；manifest 的 `album.meta_source`/`album.description` 为真实值。MCP 工具签名不变。

- [ ] **Step 1: 追加失败测试（album.py 的 meta_source 回归）**

`tests/test_cn_meta.py` 追加：

```python
def test_album_dict_keeps_real_meta_source():
    # album.py 生成 manifest 时不得再把 meta_source 硬编码为 "itunes"
    import inspect
    from app import album as album_mod
    src = inspect.getsource(album_mod._run_album)
    assert '"meta_source": "itunes"' not in src
    assert 'album.model_dump(exclude={"tracks"})' in src
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py::test_album_dict_keeps_real_meta_source -v`
Expected: FAIL（`album.py:401` 仍是硬编码）

- [ ] **Step 3: 修改 `app/main.py`**

第 14 行 import 改为（`itunes` 在 main.py 中不再被直接调用，一并移除）：

```python
from . import libraries, libops, meta, registry, storage
```

`_get_album_or_404`（66-72 行）改为：

```python
def _get_album_or_404(collection_id: str) -> AlbumInfo:
    try:
        return meta.get_album(collection_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"专辑元数据查询失败: {e}")
```

`api_album_search`（81-86 行）改为：

```python
@app.get("/api/v1/albums/search", response_model=list[AlbumSummary], dependencies=[Depends(auth)])
def api_album_search(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    try:
        return meta.search_albums(keyword=keyword, artist=artist, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"专辑搜索失败: {e}")
```

- [ ] **Step 4: 修改 `app/album.py:401`**

```python
    album_dict = album.model_dump(exclude={"tracks"})
```

同时把 `album.py` 模块 docstring 第 4 行 `- 专辑元数据来自 itunes.py（iTunes 官方 API），下载仍走 musicdl 聚合源；` 改为 `- 专辑元数据来自 meta.py 编排层（iTunes 首选，网易云/QQ 回退补充简介与中文名），下载仍走 musicdl 聚合源；`。

- [ ] **Step 5: 更新 `mcp_adapter.py` docstring（行为说明，不改签名）**

`search_albums`（167 行起）docstring 首行改为：

```python
    """按专辑名搜索专辑（iTunes 优先；覆盖不足时自动回退网易云/QQ，尽量补充中文简介）。
```

其 Returns 段改为：

```python
    Returns:
        专辑列表，含 collection_id（供 get_album_info / download_album 使用；
        iTunes 专辑为纯数字，中文源专辑带 netease:/qq: 前缀）、曲目数、发行日期、
        高清封面 URL、description（简介，可为空）、meta_source（元数据来源）。
    """
```

`get_album_info`（189 行起）docstring 改为：

```python
    """获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面、简介等。

    Args:
        collection_id: search_albums 返回的 collection_id（支持 netease:/qq: 前缀的中文源专辑；
            iTunes id 在其各 storefront 均无曲目时自动回退中文源整体接管）
    """
```

`download_album`（202 行起）docstring 的 `collection_id` 参数行改为：

```python
        collection_id: search_albums 返回的 collection_id（支持 netease:/qq: 前缀）
```

- [ ] **Step 6: 运行全部测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass（含既有 `test_libops.py` 回归）

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/album.py mcp_adapter.py tests/test_cn_meta.py
git commit -m "feat: 专辑 REST/MCP 接入元数据编排层，manifest 记录真实 meta_source"
```

---

### Task 5: 归档写简介 `app/archive.py`

**Files:**
- Modify: `app/archive.py:156-176`（`_write_album_info`）
- Test: `tests/test_cn_meta.py`（追加）

**Interfaces:**
- Consumes: manifest 的 `album.description`、`album.meta_source`（Task 4 起为真实值；旧 manifest 无 `description` 字段，`album.get("description")` 得 None，向后兼容）。
- Produces: `album_info.txt` 含「简介：」段（有 description 时）；纯中文源专辑不再出现「iTunes 原名」标注；「简介暂缺」占位行彻底移除。

- [ ] **Step 1: 追加失败测试**

`tests/test_cn_meta.py` 追加：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cn_meta.py -k write_album_info -v`
Expected: FAIL（当前实现仍有占位行、元数据来源行硬编码 iTunes）

- [ ] **Step 3: 重写 `_write_album_info`（`app/archive.py:156-176`）**

```python
def _write_album_info(album_dir: Path, album: dict, entries: list[dict],
                      display_title: str, display_artist: str) -> None:
    """生成 album_info.txt：简介取自 manifest.album.description（网易云/QQ 补充），无则省略简介段。

    「iTunes 原名」标注与 storefront 仅在元数据来自 iTunes 系（meta_source 以 itunes 开头）时输出。
    """
    meta_source = album.get("meta_source") or "itunes"
    itunes_based = meta_source.startswith("itunes")
    orig_title = album.get("title") or ""
    orig_artists = " / ".join(album.get("artists") or [])
    source_line = f"元数据来源：{meta_source} (collection {album.get('collection_id')}"
    if itunes_based and album.get("storefront"):
        source_line += f", storefront {album.get('storefront')}"
    source_line += ")"
    lines = [
        f"专辑：{display_title}" + (f"（iTunes 原名：{orig_title}）" if itunes_based and orig_title != display_title else ""),
        f"艺人：{display_artist}" + (f"（iTunes 原名：{orig_artists}）" if itunes_based and orig_artists and orig_artists != display_artist else ""),
        f"发行日期：{(album.get('release_date') or '')[:10]}",
        f"流派：{album.get('genre') or ''}",
        source_line,
        "",
        "曲目表：",
    ]
    for e in entries:
        dur = e.get("duration_s")
        dur_s = f" ({int(dur // 60)}:{int(dur % 60):02d})" if dur else ""
        prefix = f"CD{e['disc']} " if len({x['disc'] for x in entries}) > 1 else ""
        lines.append(f"{prefix}{e['track']:02d}. {t2s(e.get('title'))}{dur_s}")
    description = (album.get("description") or "").strip()
    if description:
        lines += ["", "简介：", description]
    (album_dir / "album_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4: 运行全部测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass

- [ ] **Step 5: Commit**

```bash
git add app/archive.py tests/test_cn_meta.py
git commit -m "feat: album_info.txt 写入中文简介，元数据来源按 meta_source 标注"
```

---

### Task 6: 文档与 ROADMAP 收尾

**Files:**
- Modify: `docs/API.md:96-108, 230-250`
- Modify: `docs/MCP.md:106-110`
- Modify: `ROADMAP.md:20`
- Test: 无新测试；全量回归

**Interfaces:**
- Consumes: Task 1-5 的最终行为。
- Produces: 对外文档与 ROADMAP 状态与实际行为一致。

- [ ] **Step 1: 更新 `docs/API.md`**

`AlbumSummary` 字段表（96-108 行）：`collection_id` 行说明改为「专辑 id：iTunes collectionId（纯数字），或中文源的 `netease:xxx` / `qq:xxx` 前缀 id；`get_album_info`/`download_album` 的入参」，并在 `genre` 行后追加两行：

```markdown
| `description` | string \| null | 专辑简介（来自网易云/QQ 补充，可能为空） |
| `meta_source` | string | 元数据来源：`itunes` / `netease` / `qq` / `itunes+netease` / `itunes+qq` |
```

`GET /api/v1/albums/search` 节（230-242 行）：232 行描述改为「按专辑名搜索专辑。iTunes 优先；iTunes 无结果或中文覆盖不足（关键词含中文而结果无中文）时自动回退网易云 → QQ，首个非空中文源的结果追加在 iTunes 结果之后（总数不超 limit）。」；502 说明改为「元数据接口异常」。

`GET /api/v1/albums/{collection_id}` 节（246-250 行）：248 行描述改为：

```markdown
获取专辑详情与官方曲目表。`collection_id` 按前缀路由：无前缀走 iTunes（storefront 链 CN→HK→TW→US→JP 兜底取首个有曲目的），`netease:`/`qq:` 前缀直接取对应中文源。iTunes 各 storefront 均无曲目时，自动用「专辑名+艺人」在网易云/QQ 找同专辑整体接管（含曲目表）；iTunes 命中时也会尽量合并中文源的专辑简介（`description`）与中文显示名（罗马音名按 CJK 规则替换），命中后 `meta_source` 为 `itunes+netease`/`itunes+qq`。
```

404 说明改为「各来源均无该专辑曲目」，502 说明改为「元数据接口异常」。

- [ ] **Step 2: 更新 `docs/MCP.md`**

106-107 行 `search_albums` 节改为：

```markdown
### search_albums(keyword, artist?, limit?)
按专辑名搜索专辑（iTunes 优先；覆盖不足时自动回退网易云/QQ）。返回 `collection_id`（供后续两个工具使用；iTunes 专辑为纯数字，中文源专辑带 `netease:`/`qq:` 前缀）、曲目数、发行日期、高清封面 URL、`description`（简介，可为空）、`meta_source`。
```

109-110 行 `get_album_info` 节首句改为：

```markdown
### get_album_info(collection_id)
获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面、简介等。`collection_id` 支持 `netease:`/`qq:` 前缀；iTunes id 在其各 storefront 均无曲目时自动回退中文源整体接管。**下载前建议先调用此工具向用户确认专辑版本**（同名专辑可能有 Single/EP/ deluxe 等多个版本）。
```

- [ ] **Step 3: 更新 `ROADMAP.md`（第 20 行「后续待做」）**

把 `- **后续待做**：网易云/QQ 网页接口补充中文专辑与简介（iTunes 覆盖不足时）；`get_artist_info` + 艺人 `artist.jpg` 头像；WAV→FLAC 转换` 改为：

```markdown
   - **后续待做**：`get_artist_info` + 艺人 `artist.jpg` 头像；WAV→FLAC 转换
   - **已完成补充**（2026-09-01）：中文专辑元数据补充——新增 `meta.py` 编排层 + 网易云/QQ 网页接口客户端（免登录公开 API）；iTunes 无结果/覆盖不足时自动回退，`collection_id` 支持 `netease:`/`qq:` 前缀路由；iTunes 各 storefront 无曲目时中文源整体接管；命中时合并中文简介与中文显示名（罗马音场景按发行日期+曲目数放宽匹配），`AlbumSummary` 新增 `description`/`meta_source`，`album_info.txt` 正式写入简介
```

- [ ] **Step 4: 全量回归 + 服务冒烟**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass

冒烟（可选但推荐，验证真实链路）：`.venv/bin/uvicorn app.main:app --port 8765` 启动后：
- `curl 'http://127.0.0.1:8765/api/v1/albums/search?keyword=叶惠美&artist=周杰伦'` → 结果含 `meta_source` 字段；iTunes 命中项应为 `itunes+netease` 或带简介
- `curl 'http://127.0.0.1:8765/api/v1/albums/qq:000I5jJB3blWeN'` → 返回《范特西》10 首曲目 + `desc` 简介

- [ ] **Step 5: Commit**

```bash
git add docs/API.md docs/MCP.md ROADMAP.md
git commit -m "docs: 中文专辑元数据补充的接口说明与 ROADMAP 状态更新"
```

---

## Self-Review 记录

- Spec 覆盖：回退链（Task 3）、简介合并（Task 3）、罗马音放宽（Task 3 + spec 已同步修正）、前缀路由（Task 3/4）、manifest 真实 meta_source（Task 4）、album_info.txt 简介（Task 5）、文档与 ROADMAP（Task 6）、限流降级（Task 1/2/3 测试覆盖）。
- 实测修正已回写 spec：网易云详情用 `/api/v1/album`（移动端 UA + cookie，实测 `/api/album` 易触发 -462）；QQ 艺人在 `data.singer.singerList`（非 basicInfo）；QQ `GetAlbumSongList` 的 `albumID` 传 0 可用；网易云 `publishTime` 须按 UTC+8 转日期。
- 类型一致性：`netease_meta`/`qq_meta` 的 `search_albums`/`get_album` 签名与 `itunes.py` 对齐；`meta._find_same_album` 的 `release_date`/`track_count` 参数在两个调用点（`_cn_takeover`/`_merge_cn_supplement`）一致传入。
