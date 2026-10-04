# 榜单目录（排行榜浏览 + 全量/挑选下载）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户/Agent 能在服务内浏览 QQ 音乐、网易云音乐的官方排行榜，拿到含下载地址的曲目列表后直接全量或挑选下载。

**Architecture:** 新增纯函数模块 `app/charts.py`（httpx 直连，模式同 `app/qq_meta.py`/`app/netease_meta.py`）：网易榜单复用现有 `parse_playlist`（榜单 id 即 playlist id），QQ 榜单走 `fcg_myqq_toplist.fcg`（目录）+ `fcg_v8_toplist_cp.fcg`（详情），逐曲解析复用 musicdl `QQMusicClient.parseplaylist` 的 `_parsewiththirdpartapis` → `_parsewithofficialapiv1` 模式。REST 两端点 + MCP 两薄客户端工具，下载管线零改动。

**Tech Stack:** Python 3.13 / FastAPI / httpx / pydantic v2 / musicdl(>=2.13.11) / fastmcp / pytest（monkeypatch httpx，离线运行）

**Spec:** [docs/superpowers/specs/2026-09-23-charts-catalog-design.md](../specs/2026-09-23-charts-catalog-design.md)（接口已 2026-09-23 实测可用）

## Global Constraints

- 不改下载管线、不改归档管线；Track 模型不改动，只新增 `ChartSummary`。
- 榜单 API 的 `source` 用短名 `qq` / `netease`；Track.source 保持 musicdl 客户端名（`QQMusicClient`/`NeteaseMusicClient`）不变。
- QQ 榜单详情单页上限 100：`song_num = min(limit, 100)`，`limit` 缺省取 100。
- 逐曲解析失败的条目跳过不阻断整榜；无下载地址的（VIP/付费/区域限制）自然被过滤，结果集即"可下载集"，属预期行为。
- 错误处理沿用现有惯例：源不支持/参数非法 → 400；上游接口失败 → 502 附错误说明；不做重试（避免加剧限流）。
- 单元测试全部离线：mock httpx 层与 musicdl 客户端，参照 `tests/test_cn_meta.py` 风格。
- 提交信息沿用仓库惯例：中文 conventional commits（如 `feat: …`）。
- 依赖不新增（httpx/musicdl/pydantic/fastmcp 均已在 requirements.txt）。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/schemas.py` | 修改 | 新增 `ChartSummary` 模型 |
| `app/charts.py` | 新建 | 榜单目录/曲目解析纯函数模块（本特性核心） |
| `tests/test_charts.py` | 新建 | charts 模块单元测试（httpx/musicdl mock，离线） |
| `app/main.py` | 修改 | 新增 `GET /api/v1/charts`、`GET /api/v1/charts/{source}/{chart_id}` |
| `mcp_adapter.py` | 修改 | 新增 `list_charts`、`get_chart_tracks` 两个薄客户端工具 |
| `README.md` | 修改 | REST/MCP 两张表各加两行 |
| `docs/API.md` | 修改 | 目录 + ChartSummary 数据模型 + 两个端点章节 |
| `docs/MCP.md` | 修改 | 目录 + 两个工具条目 |
| `docs/superpowers/specs/2026-09-23-charts-catalog-design.md` | 修改 | 状态从"待确认"改为"第一期已实现" |
| `ROADMAP.md` | 修改 | 2i 条目标记第一期完成（E2E 通过后） |

注：REST/MCP 为薄接线层，按项目惯例（`app/main.py`、`mcp_adapter.py` 无单测）不加自动化测试，由 Task 8 的 E2E 覆盖。

---

### Task 1: ChartSummary schema + charts 模块骨架 + QQ 榜单目录

**Files:**
- Modify: `app/schemas.py`（在 `SourceInfo` 类之后插入）
- Create: `app/charts.py`
- Test: `tests/test_charts.py`

**Interfaces:**
- Produces（后续任务依赖）：
  - `app.schemas.ChartSummary`：`id: str / source: str / name: str / cover_url: Optional[str] / track_count: Optional[int] / extra: dict[str, Any]`
  - `app/charts.py` 常量：`SOURCES = ("qq", "netease")`、`QQ_LIST_URL`、`QQ_DETAIL_URL`、`NETEASE_LIST_URL`、`_QQ_PAGE_CAP = 100`
  - `list_charts(source: str) -> list[ChartSummary]`：source 非 qq/netease 抛 `ValueError`；上游业务错误码抛 `LookupError`；网络错误向上抛 httpx 异常

- [ ] **Step 1: 写失败测试**

新建 `tests/test_charts.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.charts'`）

- [ ] **Step 3: 实现 ChartSummary 与 charts 骨架**

`app/schemas.py` 在 `SourceInfo` 类（约 36-44 行）之后插入：

```python
class ChartSummary(BaseModel):
    """榜单摘要（QQ / 网易云排行榜目录项）。"""
    id: str = Field(description="榜单 id：QQ 为 topid，网易云为 playlist id")
    source: str = Field(description="榜单来源：qq / netease")
    name: str = Field(description="榜单名")
    cover_url: Optional[str] = Field(default=None, description="榜单封面 URL")
    track_count: Optional[int] = Field(default=None, description="曲目数（接口提供时；目录接口通常不给）")
    extra: dict[str, Any] = Field(default_factory=dict, description="源特有附加信息（试听数/更新频率/前三首预览等）")
```

新建 `app/charts.py`：

```python
"""榜单目录：QQ / 网易云排行榜浏览与曲目解析。

实测要点（2026-09-23）：
- 网易云排行榜本质是歌单：GET music.163.com/api/toplist（免登录，PC UA + Referer）返回全部榜单，
  id 即 playlist id，榜单详情复用 app.playlist.parse_playlist（全量解析，无法分页）；
- QQ 排行榜为独立体系：fcg_myqq_toplist.fcg 取目录（topList：id/topTitle/picUrl/listenCount），
  fcg_v8_toplist_cp.fcg 取详情（songlist[].data 与歌单条目同构，song_begin/song_num 可分页，
  单页上限 100）；
- 榜单详情接口只返回元数据不含下载地址，需逐曲解析：复用 QQMusicClient.parseplaylist 的
  逐曲模式（_parsewiththirdpartapis → _parsewithofficialapiv1），无下载地址的（VIP/付费/区域）
  自然被过滤，结果集即"可下载集"。
"""
from __future__ import annotations

from contextlib import suppress

import httpx

from .playlist import parse_playlist
from .registry import build_client
from .schemas import ChartSummary, Track
from .search import cache_tracks, normalize_song

SOURCES = ("qq", "netease")

QQ_LIST_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_myqq_toplist.fcg"
QQ_DETAIL_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg"
NETEASE_LIST_URL = "https://music.163.com/api/toplist"

_QQ_HEADERS = {"Referer": "https://y.qq.com", "User-Agent": "Mozilla/5.0"}
_NE_HEADERS = {
    "Referer": "https://music.163.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}
_TIMEOUT = httpx.Timeout(10.0)
_QQ_PAGE_CAP = 100  # fcg_v8_toplist_cp 单页上限


def list_charts(source: str) -> list[ChartSummary]:
    """列出指定平台的官方排行榜目录；source 非 qq/netease 抛 ValueError。"""
    if source == "qq":
        return _qq_list_charts()
    if source == "netease":
        return _netease_list_charts()
    raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")


def _qq_list_charts() -> list[ChartSummary]:
    r = httpx.get(QQ_LIST_URL, headers=_QQ_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise LookupError(f"QQ 榜单目录接口返回错误（code={data.get('code')}）")
    out = []
    for t in ((data.get("data") or {}).get("topList")) or []:
        if not t.get("id"):
            continue
        preview = [f"{s.get('singername', '')}-{s.get('songname', '')}"
                   for s in (t.get("songList") or [])[:3]]
        out.append(ChartSummary(
            id=str(t["id"]),
            source="qq",
            name=t.get("topTitle") or "未知榜单",
            cover_url=t.get("picUrl"),
            track_count=None,
            extra={"listen_count": t.get("listenCount"), "update": t.get("update"),
                   "preview": preview},
        ))
    return out
```

`_netease_list_charts` 与 `get_chart_tracks` 在 Task 2/3/4 补齐；本任务先让模块可 import，在文件末尾放占位：

```python
def _netease_list_charts() -> list[ChartSummary]:
    raise NotImplementedError  # Task 2 实现


def get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    """取榜单曲目（标准化 Track，含下载地址，已落缓存可直接 submit_download）。"""
    if source not in SOURCES:
        raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")
    raise NotImplementedError  # Task 3/4 实现
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/schemas.py app/charts.py tests/test_charts.py
git commit -m "feat: 榜单目录 charts 模块骨架与 QQ 榜单目录（ChartSummary + list_charts）"
```

---

### Task 2: 网易榜单目录

**Files:**
- Modify: `app/charts.py`（替换 `_netease_list_charts` 占位）
- Test: `tests/test_charts.py`

**Interfaces:**
- Consumes: Task 1 的 `ChartSummary`、`NETEASE_LIST_URL`、`_NE_HEADERS`、`_TIMEOUT`
- Produces: `_netease_list_charts() -> list[ChartSummary]`（`extra` 含 `update_frequency`、`description`；反爬限流 code -462 等异常码抛 `LookupError`）

- [ ] **Step 1: 写失败测试**

`tests/test_charts.py` 追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_charts.py -k netease -v`
Expected: FAIL（`NotImplementedError`）

- [ ] **Step 3: 实现 `_netease_list_charts`**

`app/charts.py` 中把 `_netease_list_charts` 占位替换为：

```python
def _netease_list_charts() -> list[ChartSummary]:
    r = httpx.get(NETEASE_LIST_URL, headers=_NE_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise LookupError(f"网易云榜单目录接口返回错误（code={data.get('code')}），可能被限流，请稍后重试")
    out = []
    for t in data.get("list") or []:
        if not t.get("id"):
            continue
        out.append(ChartSummary(
            id=str(t["id"]),
            source="netease",
            name=t.get("name") or "未知榜单",
            cover_url=t.get("coverImgUrl"),
            track_count=None,
            extra={"update_frequency": t.get("updateFrequency"),
                   "description": (t.get("description") or "").strip() or None},
        ))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/charts.py tests/test_charts.py
git commit -m "feat: 网易榜单目录（/api/toplist 字段映射与限流错误处理）"
```

---

### Task 3: QQ 榜单曲目解析

**Files:**
- Modify: `app/charts.py`（`get_chart_tracks` 分派 + `_qq_chart_tracks` + `_resolve_qq_track`）
- Test: `tests/test_charts.py`

**Interfaces:**
- Consumes: `build_client(["QQMusicClient"]).music_clients["QQMusicClient"]`（`app/registry.py:113`）；`normalize_song` / `cache_tracks`（`app/search.py`）
- Produces:
  - `get_chart_tracks(source, chart_id, limit=None) -> list[Track]`：分派入口（Task 4 补 netease 分支）
  - `_qq_chart_tracks(chart_id, limit) -> list[Track]`：QQ 详情请求参数 `song_num=min(limit,100)`（limit 缺省 100），`song_begin=0`；结果 `cache_tracks` 落缓存
  - `_resolve_qq_track(client, search_result: dict) -> SongInfo | None`：thirdpart 先试 → official 兜底（异常 suppress），两者都无有效下载地址返回 None

关键实现要点（来自 musicdl `qq.py:494-505` parseplaylist 逐曲模式）：`lossless_quality_is_sufficient = not bool(client.default_cookies)`；每条 `songlist[].data` 即 search_result（含 songmid/songname/singer/albumname/interval）；用 `song_info.with_valid_download_url` 判定取舍。

- [ ] **Step 1: 写失败测试**

`tests/test_charts.py` 追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_charts.py -k "qq_chart or resolve" -v`
Expected: FAIL（`NotImplementedError` / `AttributeError`）

- [ ] **Step 3: 实现 QQ 榜单曲目**

`app/charts.py` 中把 `get_chart_tracks` 占位替换为分派，并新增两个函数：

```python
def get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    """取榜单曲目（标准化 Track，含下载地址，已落缓存可直接 submit_download）。

    QQ 侧 song_num 单页上限 100，limit 缺省/超出均按 100；网易云为全量解析后截断（Task 4）。
    逐曲解析失败的条目跳过，不阻断整榜。
    """
    if source == "qq":
        return _qq_chart_tracks(chart_id, limit)
    if source == "netease":
        return _netease_chart_tracks(chart_id, limit)
    raise ValueError(f"不支持的榜单源：{source}（可选：{', '.join(SOURCES)}）")


def _qq_chart_tracks(chart_id: str, limit: int | None) -> list[Track]:
    song_num = min(limit, _QQ_PAGE_CAP) if limit else _QQ_PAGE_CAP
    r = httpx.get(QQ_DETAIL_URL, params={"topid": chart_id, "tpl": 3, "page": "detail",
                                         "type": "top", "song_begin": 0, "song_num": song_num},
                  headers=_QQ_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise LookupError(f"QQ 榜单详情接口返回错误（topid={chart_id}, code={data.get('code')}）")
    client = build_client(["QQMusicClient"]).music_clients.get("QQMusicClient")
    if client is None:
        raise LookupError("QQ 源不可用（未配置 cookies 或登录凭证失效），无法解析榜单曲目下载地址")
    tracks: list[Track] = []
    for item in data.get("songlist") or []:
        sr = item.get("data") or item  # toplist_cp 条目为 {"data": {...}} 包裹，兼容裸条目
        try:
            song = _resolve_qq_track(client, sr)
        except Exception:
            continue  # 逐曲解析失败跳过，不阻断整榜（与 musicdl parseplaylist 口径一致）
        if song is None:
            continue
        t = normalize_song("QQMusicClient", song)
        if t:
            tracks.append(t)
    cache_tracks(tracks)  # 落缓存，submit_download 可仅按 id 提交
    return tracks


def _resolve_qq_track(client, search_result: dict):
    """复用 QQMusicClient.parseplaylist 的逐曲解析模式（musicdl qq.py:494-505）：
    thirdpart 先试（无 cookies 时可出无损），official 兜底；official 异常时回退 thirdpart 结果。
    两者都无有效下载地址返回 None（VIP/付费/区域限制），调用方跳过。"""
    song_info_flac = client._parsewiththirdpartapis(search_result=search_result)
    song_info = None
    with suppress(Exception):
        song_info = client._parsewithofficialapiv1(
            search_result=search_result, song_info_flac=song_info_flac,
            lossless_quality_is_sufficient=not bool(client.default_cookies))
    if song_info is not None and getattr(song_info, "with_valid_download_url", False):
        return song_info
    if song_info_flac is not None and getattr(song_info_flac, "with_valid_download_url", False):
        return song_info_flac
    return None
```

同时需要 netease 分支的临时占位（Task 4 替换）：

```python
def _netease_chart_tracks(chart_id: str, limit: int | None) -> list[Track]:
    raise NotImplementedError  # Task 4 实现
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add app/charts.py tests/test_charts.py
git commit -m "feat: QQ 榜单曲目解析（toplist_cp 分页 + 复用 QQMusicClient 逐曲解析模式）"
```

---

### Task 4: 网易榜单曲目

**Files:**
- Modify: `app/charts.py`（替换 `_netease_chart_tracks` 占位）
- Test: `tests/test_charts.py`

**Interfaces:**
- Consumes: `app.playlist.parse_playlist(url, source)`（`app/playlist.py:11`，内部已 `cache_tracks`）
- Produces: `_netease_chart_tracks(chart_id, limit) -> list[Track]`：URL 固定拼 `https://music.163.com/playlist?id={chart_id}`，source 传 `"NeteaseMusicClient"`，`limit` 为返回后截断

- [ ] **Step 1: 写失败测试**

`tests/test_charts.py` 追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_charts.py -k netease_chart -v`
Expected: FAIL（`NotImplementedError`）

- [ ] **Step 3: 实现 `_netease_chart_tracks`**

`app/charts.py` 中把 `_netease_chart_tracks` 占位替换为：

```python
def _netease_chart_tracks(chart_id: str, limit: int | None) -> list[Track]:
    # 网易云榜单 id 即 playlist id，详情复用歌单解析（全量逐曲，无法分页，大榜单较慢）；
    # parse_playlist 内部已 cache_tracks
    tracks = parse_playlist(url=f"https://music.163.com/playlist?id={chart_id}",
                            source="NeteaseMusicClient")
    return tracks[:limit] if limit else tracks
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_charts.py -v`
Expected: 14 passed

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过（既有用例不受影响）

- [ ] **Step 6: Commit**

```bash
git add app/charts.py tests/test_charts.py
git commit -m "feat: 网易榜单曲目（复用 parse_playlist，limit 返回后截断）"
```

---

### Task 5: REST 端点

**Files:**
- Modify: `app/main.py`（import 行 + 在 `api_playlist` 之后插入两个端点）

**Interfaces:**
- Consumes: `charts.SOURCES`、`charts.list_charts`、`charts.get_chart_tracks`、`schemas.ChartSummary`
- Produces:
  - `GET /api/v1/charts?source=` → `list[ChartSummary]`；source 省略返回两平台合并，单平台失败不阻断另一平台，全失败 → 502；source 非法 → 400
  - `GET /api/v1/charts/{source}/{chart_id}?limit=` → `list[Track]`；ValueError → 400，其余异常 → 502

- [ ] **Step 1: 修改 import**

`app/main.py` 第 10 行的 schemas import 加入 `ChartSummary`（按字母序插在 `BackfillLyricsRequest` 之后、`CleanupLibraryRequest` 之前）；第 14 行的模块 import 行改为含 `charts`：

```python
from . import backfill, charts as charts_svc, libraries, libops, meta, registry, storage
```

- [ ] **Step 2: 实现两个端点**

`app/main.py` 在 `api_playlist` 函数（约 60-65 行）之后插入：

```python
@app.get("/api/v1/charts", response_model=list[ChartSummary], dependencies=[Depends(auth)])
def api_charts(source: str | None = None) -> list[ChartSummary]:
    if source is not None and source not in charts_svc.SOURCES:
        raise HTTPException(status_code=400,
                            detail=f"不支持的榜单源：{source}（可选：{', '.join(charts_svc.SOURCES)}）")
    sources = [source] if source else list(charts_svc.SOURCES)
    charts, errors = [], []
    for s in sources:
        try:
            charts.extend(charts_svc.list_charts(s))
        except Exception as e:
            errors.append(f"{s}: {e}")
    if not charts and errors:
        raise HTTPException(status_code=502, detail=f"榜单目录获取失败：{'; '.join(errors)}")
    return charts


@app.get("/api/v1/charts/{source}/{chart_id}", response_model=list[Track], dependencies=[Depends(auth)])
def api_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    try:
        return charts_svc.get_chart_tracks(source, chart_id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"榜单曲目获取失败（{source}/{chart_id}）：{e}")
```

- [ ] **Step 3: 冒烟验证（离线 import + 路由注册）**

Run: `.venv/bin/python -c "from app.main import app; print([r.path for r in app.routes if 'charts' in r.path])"`
Expected: `['/api/v1/charts', '/api/v1/charts/{source}/{chart_id}']`

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "feat: 榜单 REST 端点（GET /api/v1/charts 目录与曲目，单源失败降级合并）"
```

---

### Task 6: MCP 工具

**Files:**
- Modify: `mcp_adapter.py`（在 `parse_playlist` 工具之后插入）

**Interfaces:**
- Consumes: REST `GET /api/v1/charts`、`GET /api/v1/charts/{source}/{chart_id}`
- Produces: MCP 工具 `list_charts(source?) -> dict`、`get_chart_tracks(source, chart_id, limit?) -> dict`（返回精简字段，不含 raw，与 `search_tracks` 口径一致）

- [ ] **Step 1: 实现两个工具**

`mcp_adapter.py` 在 `parse_playlist` 工具函数（约 106-118 行）之后插入：

```python
@mcp.tool()
def list_charts(source: str | None = None) -> dict:
    """浏览 QQ 音乐 / 网易云音乐的官方排行榜目录（榜单名、id、封面、试听数/更新频率）。

    Args:
        source: 平台，qq 或 netease；留空返回两平台合并列表（单平台失败不阻断另一平台）
    Returns:
        榜单列表：id 供 get_chart_tracks 使用，source/name/cover_url/extra（试听数、前三首预览等）。
    """
    params = {"source": source} if source else {}
    with _client() as c:
        r = c.get("/api/v1/charts", params=params)
        r.raise_for_status()
        charts = r.json()
    return {"total": len(charts), "charts": charts}


@mcp.tool()
def get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> dict:
    """获取排行榜曲目（含下载地址，已落缓存，可直接用 submit_download 全量或挑选下载）。

    Args:
        source: 平台，qq 或 netease
        chart_id: 榜单 id（取自 list_charts）
        limit: 只取前 N 首（可选；QQ 单页上限 100 超出按 100 截断；网易云全量解析后截断，
            大榜单为同步阻塞解析，较慢）
    Returns:
        曲目列表（id/source/title/artists/album/ext/quality/size_bytes/duration_s/cover_url）。
        全量下载：submit_download(tracks=[{"id": t["id"]} for t in tracks], subdir="榜单-XX")；
        挑选下载：传子集即可。无下载地址的 VIP/付费曲目已被过滤，数量可能少于名义曲目数，属预期。
    """
    params: dict[str, Any] = {}
    if limit:
        params["limit"] = limit
    with _client() as c:
        r = c.get(f"/api/v1/charts/{source}/{chart_id}", params=params, timeout=600)
        r.raise_for_status()
        tracks = r.json()
    return {"total": len(tracks),
            "tracks": [{"id": t["id"], "source": t["source"], "title": t["title"],
                        "artists": t["artists"], "album": t["album"], "ext": t["ext"],
                        "quality": t["quality"], "size_bytes": t["size_bytes"],
                        "duration_s": t["duration_s"], "cover_url": t["cover_url"]} for t in tracks]}
```

注：QQ 逐曲解析为同步阻塞网络密集操作，`timeout=600` 与 `backfill_lyrics` 工具口径一致。

- [ ] **Step 2: 冒烟验证**

Run: `.venv/bin/python -c "import asyncio, mcp_adapter; print(asyncio.run(mcp_adapter.mcp.get_tools()).keys() if hasattr(mcp_adapter.mcp, 'get_tools') else [t.name for t in mcp_adapter.mcp._tools])" 2>/dev/null || .venv/bin/python -c "import mcp_adapter; print('import ok')"`
Expected: 输出包含 `list_charts` 与 `get_chart_tracks`（或至少 import ok）

- [ ] **Step 3: Commit**

```bash
git add mcp_adapter.py
git commit -m "feat: MCP 榜单工具（list_charts / get_chart_tracks 薄客户端）"
```

---

### Task 7: 文档更新（README / API / MCP / 设计文档状态）

**Files:**
- Modify: `README.md`、`docs/API.md`、`docs/MCP.md`、`docs/superpowers/specs/2026-09-23-charts-catalog-design.md`

- [ ] **Step 1: README.md**

REST API 表（约 79 行 `/api/v1/playlist` 行之后）插入：

```markdown
| GET | `/api/v1/charts?source=…` | 榜单目录（QQ/网易云排行榜；source 省略返回合并列表） |
| GET | `/api/v1/charts/{source}/{chart_id}?limit=…` | 榜单曲目（已缓存，可直接全量/挑选提交下载） |
```

MCP 工具表（`parse_playlist` 行之后）插入：

```markdown
| `list_charts(source?)` | 榜单目录（QQ/网易云排行榜） |
| `get_chart_tracks(source, chart_id, limit?)` | 榜单曲目（可直接全量/挑选提交下载） |
```

- [ ] **Step 2: docs/API.md**

目录（约 164 行 `### GET /api/v1/playlist` 条目之后）插入两行目录项；数据模型章节（`### Track` 之后）新增：

```markdown
### ChartSummary（榜单摘要）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 榜单 id：QQ 为 topid，网易云为 playlist id |
| source | string | 榜单来源：`qq` / `netease` |
| name | string | 榜单名 |
| cover_url | string\|null | 榜单封面 URL |
| track_count | int\|null | 曲目数（目录接口通常不给，为 null） |
| extra | object | 源特有附加信息：QQ 含 listen_count/update/preview（前三首"艺人-曲名"）；网易云含 update_frequency/description |
```

接口列表章节（`### GET /api/v1/playlist` 之后）新增：

```markdown
### GET /api/v1/charts

浏览 QQ 音乐 / 网易云音乐的官方排行榜目录。

**Query 参数**

| 参数 | 必填 | 说明 |
|---|---|---|
| source | 否 | `qq` 或 `netease`；省略返回两平台合并列表（单平台失败不阻断另一平台） |

**响应**：`list[ChartSummary]`

**错误**：source 非法 → 400；全部平台失败 → 502（附各平台错误说明）

### GET /api/v1/charts/{source}/{chart_id}

获取排行榜曲目（标准化 Track，含下载地址，已落缓存，可直接 `POST /api/v1/downloads` 全量或挑选提交）。

**路径参数**：`source`（qq/netease）、`chart_id`（取自 `/api/v1/charts` 的 id）

**Query 参数**

| 参数 | 必填 | 说明 |
|---|---|---|
| limit | 否 | 只取前 N 首；QQ 单页上限 100（缺省 100，超出按 100 截断），网易云为全量解析后截断 |

**响应**：`list[Track]`（字段同搜索接口）

**说明**：榜单详情接口只返回元数据，服务端逐曲解析下载地址（同步阻塞，大榜单较慢，客户端需容忍长超时）；无下载地址的 VIP/付费/区域限制曲目被过滤，返回数量可能少于名义曲目数，属预期。

**错误**：source 非法 → 400；上游接口失败/限流 → 502

**典型流程**：`GET /api/v1/charts` → `GET /api/v1/charts/qq/4?limit=50` → `POST /api/v1/downloads`（tracks 只传 id 列表，subdir 如 `榜单-巅峰榜流行指数-2026-10-04`）
```

同时在"注意事项与已知限制"章节（约 547 行）追加一条：

```markdown
- 榜单曲目解析（`/api/v1/charts/{source}/{chart_id}`）为同步阻塞：QQ 逐曲解析下载地址（单页上限 100），网易云复用歌单全量解析，大榜单耗时长，客户端需容忍长超时
```

- [ ] **Step 3: docs/MCP.md**

目录与工具说明章节（`### parse_playlist(url, source?)` 之后）各插入：

```markdown
### list_charts(source?)

浏览 QQ 音乐 / 网易云官方排行榜目录；source 留空返回两平台合并列表。返回榜单 id/name/cover_url/extra（QQ 含试听数与前三首预览，网易含更新频率）。

### get_chart_tracks(source, chart_id, limit?)

获取排行榜曲目（含下载地址，已缓存 1 小时）。全量下载：`submit_download(tracks=[{"id": …}], subdir="榜单-XX")`；挑选下载传 id 子集。QQ 单页上限 100；VIP/付费无地址曲目已被过滤，数量偏少属预期。
```

并在"五、典型调用流程"追加一节：

```markdown
### 榜单浏览与下载

1. `list_charts()` 选榜单（如 QQ 巅峰榜·流行指数 id=4）
2. `get_chart_tracks(source="qq", chart_id="4", limit=50)` 拿曲目
3. `submit_download(tracks=[{"id": t.id} for t in tracks], subdir="榜单-巅峰榜流行指数")` 全量或挑选提交
```

- [ ] **Step 4: 设计文档状态更新**

`docs/superpowers/specs/2026-09-23-charts-catalog-design.md` 第 4 行 `状态：待确认` 改为 `状态：第一期已实现（2026-10-04，见 docs/superpowers/plans/2026-10-04-charts-catalog.md）`。

- [ ] **Step 5: Commit**

```bash
git add README.md docs/API.md docs/MCP.md docs/superpowers/specs/2026-09-23-charts-catalog-design.md
git commit -m "docs: 榜单目录 API/MCP/README 文档与设计文档状态更新"
```

---

### Task 8: 主链路 E2E 验证 + ROADMAP 更新

**Files:**
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: 全部前置任务

- [ ] **Step 1: 启动本地服务**

```bash
.venv/bin/python -m uvicorn app.main:app --port 8765 &
sleep 2
```

（若 8765 已被占用则换端口，后续 curl 同步替换；config.yaml 的 api_key 若非空，所有 curl 加 `-H "X-API-Key: <key>"`。）

- [ ] **Step 2: 榜单目录冒烟**

```bash
curl -s 'http://127.0.0.1:8765/api/v1/charts?source=qq' | head -c 600
curl -s 'http://127.0.0.1:8765/api/v1/charts?source=netease' | head -c 600
curl -s 'http://127.0.0.1:8765/api/v1/charts' | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), sorted({c['source'] for c in d}))"
```

Expected: QQ/网易各返回非空榜单列表；合并列表含 `['netease', 'qq']` 两源；source 非法时 `curl -s 'http://127.0.0.1:8765/api/v1/charts?source=xx'` 返回 400。

- [ ] **Step 3: 榜单曲目 + 挑选下载**

```bash
# QQ 榜取前 3 首（逐曲解析约需十几秒，容忍长超时）
curl -s --max-time 300 'http://127.0.0.1:8765/api/v1/charts/qq/4?limit=3' | python3 -c "import json,sys; d=json.load(sys.stdin); print([(t['id'], t['title'], t['ext']) for t in d])"
# 挑第 1 首提交下载（id 从上一步输出取）
curl -s -X POST 'http://127.0.0.1:8765/api/v1/downloads' -H 'Content-Type: application/json' \
  -d '{"tracks": [{"id": "<上一步的id>"}], "subdir": "charts-e2e"}'
# 轮询任务直到 success/failed
curl -s 'http://127.0.0.1:8765/api/v1/downloads/<task_id>'
```

Expected: 曲目返回含 `QQMusicClient:` 前缀 id 与 ext；下载任务最终 success（若该曲因 VIP 被过滤则换榜单/换曲目重试一次；长期全部失败才视为问题）。

- [ ] **Step 4: 搜索回归冒烟**

```bash
curl -s 'http://127.0.0.1:8765/api/v1/search?keyword=周杰伦&limit=2' | head -c 300
.venv/bin/pytest -v
```

Expected: 搜索有结果；pytest 全绿。

- [ ] **Step 5: 停服务并更新 ROADMAP**

```bash
kill %1  # 停掉 uvicorn
```

`ROADMAP.md` 第 73 行 2i 标题行：

`2i. **榜单目录（排行榜浏览 + 全量/挑选下载）** ⬜ 待启动（2026-09-23 设计文档已落档，待评审）`

改为：

`2i. **榜单目录（排行榜浏览 + 全量/挑选下载）** ✅ 第一期已完成（2026-10-04）`

并在该条目第一期行（第 76 行）末尾追加：`（已实现并 E2E 验证通过：QQ fcg_myqq_toplist/fcg_v8_toplist_cp 逐曲解析复用 QQMusicClient parseplaylist 模式，网易榜单 id 即 playlist id 复用 parse_playlist；REST 两端点 + MCP 两工具）`。

- [ ] **Step 6: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: ROADMAP 2i 榜单目录第一期标记完成（E2E 验证通过）"
```

---

## Self-Review 记录

- **Spec coverage**：榜单目录（Task 1/2）✓、榜单曲目含逐曲解析与缓存（Task 3/4）✓、REST 两端点含 400/502 口径（Task 5）✓、MCP 两工具（Task 6）✓、下载零改动复用 submit_download（Task 8 E2E 验证）✓、测试（各 Task TDD）✓、README/API/MCP 文档（Task 7）✓。第二期歌单搜索、第三期异步化明确不在本计划。
- **Placeholder scan**：无 TBD/TODO；所有代码步骤含完整可粘贴代码。
- **Type consistency**：`ChartSummary` 字段（Task 1 定义）与 Task 5/6/7 使用一致；`list_charts(source)` / `get_chart_tracks(source, chart_id, limit)` 签名在 Task 3/4/5/6 一致；`_resolve_qq_track(client, search_result)` 在 Task 3 测试与实现一致；`SOURCES = ("qq", "netease")` 在 Task 1 定义、Task 5 使用。
