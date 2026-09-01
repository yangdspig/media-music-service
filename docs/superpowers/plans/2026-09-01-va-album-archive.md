# 合集（Various Artists）专辑归档实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 合集/原声带专辑统一归档到 `{库根}/群星/{专辑}/`，tag 保留逐曲艺人并写 COMPILATION 标记，Navidrome 可正确按合集分组。

**Architecture:** 归档时（`archive_album`）按 VA 名单判定合集（显式参数可覆盖），命中后目录根固定为「群星」，`_write_tags` 扩展 `track_artist`/`compilation` 两个可选参数承载逐曲艺人与合集标记；`replace_album_track` 顺带改为沿用旧文件 ARTIST/COMPILATION。REST/MCP 仅加 `compilation` 可选参数，无其他签名变化。

**Tech Stack:** Python 3.11+、FastAPI、mutagen（既有依赖，不新增）、pytest（`.venv/bin/python -m pytest`）。

**Spec:** `docs/superpowers/specs/2026-09-01-va-album-archive-design.md`

## Global Constraints

- 不新增第三方依赖。
- 合集目录名固定「群星」，VA 名单写死代码：归一化（`t2s` → `strip` → `lower`）后比对 `{"various artists", "va", "群星", "华语群星", "合辑"}`。
- 判定优先级：`compilation` 参数 > 显式 `artist` 参数 > 名单自动判定。
- 普通专辑（非合集）的归档行为逐字节不变——既有测试必须全绿。
- 合集专辑不写艺人头像 `artist.*`。
- 代码注释用中文，风格对齐 `app/archive.py` 现有写法。
- MP3 的 TCMP 帧 mutagen 无内置：在写 tag 的分支内定义 `TextFrame` 子类并注册进 `mutagen.id3.Frames`（先写后读的场景下注册即生效）。
- 测试用假 .mp3 字节文件走真实 ID3 写读（mutagen `ID3().save()` 对任意字节文件可用）；FLAC 分支不做真实文件测试（无生成手段），行为由 MP3 路径等价覆盖。
- 每任务结束单独 commit，commit message 用中文 conventional 格式。
- 测试命令统一：`.venv/bin/python -m pytest tests/ -v`（仓库根目录）。

---

### Task 1: `_write_tags` 扩展 + 合集判定函数

**Files:**
- Modify: `app/archive.py:70-147`（`_write_tags`）与模块级新增 `_VA_NAMES`/`_VA_ARTIST`/`_is_compilation`
- Test: `tests/test_archive_va.py`（新建）

**Interfaces:**
- Consumes: 现有 `_write_tags`、`t2s`（`app/album.py`）。
- Produces:
  - `_write_tags(path, title, artist, album_title, date="", numbers=None, cover_bytes=None, lyric_text=None, strip_numbers=False, track_artist=None, compilation=False)`——`track_artist` 非空时 ARTIST=track_artist（否则=artist），ALBUMARTIST 恒=artist；`compilation=True` 时 FLAC 写 `COMPILATION=1`、MP3 写 `TCMP` 文本帧 "1"
  - `_VA_ARTIST = "群星"`；`_is_compilation(album: dict, display_artist: str, explicit_artist: str | None, compilation: bool | None) -> bool`
  - FLAC 白名单 keep 集合新增 `"COMPILATION"`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_archive_va.py`：

```python
"""合集（Various Artists）专辑归档单元测试：假 .mp3 走真实 ID3 写读。"""
import pytest

from app import archive
from app.archive import _VA_ARTIST, _is_compilation, _write_tags


@pytest.mark.parametrize("name,hit", [
    ("Various Artists", True), ("various artists", True), ("VA", True),
    ("群星", True), ("华语群星", True), ("合辑", True),
    ("周杰伦", False), ("", False),
])
def test_is_compilation_name_list(name, hit):
    album = {"artists": [name] if name else []}
    assert _is_compilation(album, name, explicit_artist=None, compilation=None) is hit


def test_is_compilation_priority():
    va_album = {"artists": ["Various Artists"]}
    # compilation 参数最高优先级
    assert _is_compilation(va_album, "Various Artists", None, compilation=False) is False
    assert _is_compilation({"artists": ["周杰伦"]}, "周杰伦", None, compilation=True) is True
    # 显式 artist 参数抑制名单判定
    assert _is_compilation(va_album, "某人", explicit_artist="某人", compilation=None) is False
    # 原始艺人命中名单也算（显示艺人被覆盖链改成中文名的情况）
    assert _is_compilation(va_album, "某中文名", None, None) is True


def test_write_tags_mp3_track_artist_and_compilation(tmp_path):
    f = tmp_path / "03 - 六月的雨.mp3"
    f.write_bytes(b"fake mp3 bytes")
    _write_tags(f, "六月的雨", _VA_ARTIST, "仙剑奇侠传 电视原声带",
                track_artist="胡歌", compilation=True)
    from mutagen.id3 import ID3
    tags = ID3(f)
    assert str(tags["TPE1"].text[0]) == "胡歌"      # ARTIST = 逐曲艺人
    assert str(tags["TPE2"].text[0]) == "群星"      # ALBUMARTIST = 群星
    assert tags.getall("TCMP") and str(tags["TCMP"].text[0]) == "1"


def test_write_tags_mp3_normal_unchanged(tmp_path):
    f = tmp_path / "01 - 晴天.mp3"
    f.write_bytes(b"fake mp3 bytes")
    _write_tags(f, "晴天", "周杰伦", "叶惠美")
    from mutagen.id3 import ID3
    tags = ID3(f)
    assert str(tags["TPE1"].text[0]) == "周杰伦"
    assert str(tags["TPE2"].text[0]) == "周杰伦"
    assert not tags.getall("TCMP")  # 非合集不写 TCMP
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_archive_va.py -v`
Expected: FAIL（`ImportError: cannot import name '_is_compilation'`）

- [ ] **Step 3: 实现 `app/archive.py` 改动**

模块级（`_TAGGABLE_EXTS` 之后）新增：

```python
# 合集（Various Artists）判定：归一化（t2s→strip→lower）后比对名单
_VA_ARTIST = "群星"
_VA_NAMES = {"various artists", "va", "群星", "华语群星", "合辑"}


def _is_compilation(album: dict, display_artist: str, explicit_artist: str | None,
                    compilation: bool | None) -> bool:
    """合集判定：compilation 参数 > 显式 artist 参数（视为普通专辑）> VA 名单。

    名单比对两个值：解析后的显示艺人与 manifest 原始艺人（覆盖链可能把
    "Various Artists" 改成中文显示名，原始值仍是 VA）。
    """
    if compilation is not None:
        return compilation
    if explicit_artist:
        return False
    candidates = [display_artist, (album.get("artists") or [""])[0]]
    return any(t2s(c).strip().lower() in _VA_NAMES for c in candidates if c)
```

`_write_tags`（`archive.py:70-147`）签名与两处分支改动：

```python
def _write_tags(path: Path, title: str, artist: str, album_title: str, date: str = "",
                numbers: dict[str, str] | None = None,
                cover_bytes: bytes | None = None, lyric_text: str | None = None,
                strip_numbers: bool = False,
                track_artist: str | None = None, compilation: bool = False) -> None:
    """按库约定重写 tag 并嵌封面/歌词（仅 flac/mp3）。artist/album_title 为解析后的显示名。

    numbers 为序号类 tag（TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL），专辑归档传入，
    单曲归档传 None（不写序号）；strip_numbers=True 时显式清除已有序号类 tag（单曲迁移用）；
    date 为空则不写 DATE。
    track_artist 非空时 ARTIST 写逐曲艺人（合集专辑用），否则 ARTIST=artist；
    compilation=True 时写合集标记（FLAC: COMPILATION=1；MP3: TCMP 文本帧）。
    """
    ext = path.suffix.lstrip(".").lower()
    title = t2s(title or "")
    track_artist = t2s(track_artist) if track_artist else None
    numbers = numbers or {}
    comment = settings.archive_comment
```

FLAC 分支：keep 集合加 `"COMPILATION"`，`ARTIST`/`ALBUMARTIST` 与合集标记改为：

```python
        keep = {"ARTIST", "ALBUMARTIST", "ALBUM", "TITLE", "DATE",
                "TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL", "COMMENT", "LYRICS",
                "COMPILATION"}
        for key in list(audio.keys()):
            if key.upper() not in keep:
                del audio[key]
        audio["ARTIST"] = track_artist or artist
        audio["ALBUMARTIST"] = artist
        if compilation:
            audio["COMPILATION"] = "1"
```

（其余 FLAC 分支行不变。）

MP3 分支：`from mutagen.id3 import ...` 行加 `TextFrame, Frames`；TPE1 行改为逐曲艺人；合集标记：

```python
        from mutagen.id3 import APIC, COMM, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT, ID3, TextFrame, Frames
        try:
            audio = ID3(path)
        except Exception:
            audio = ID3()
        audio.delall("TPE1"); audio.add(TPE1(encoding=3, text=track_artist or artist))
        audio.delall("TPE2"); audio.add(TPE2(encoding=3, text=artist))
        if compilation:
            class TCMP(TextFrame):
                """iTunes 合集标记帧（Navidrome 识别）；mutagen 无内置，注册后生效。"""

            Frames["TCMP"] = TCMP
            audio.delall("TCMP"); audio.add(TCMP(encoding=3, text="1"))
```

（其余 MP3 分支行不变。）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_archive_va.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add app/archive.py tests/test_archive_va.py
git commit -m "feat: _write_tags 支持逐曲艺人与合集标记，VA 名单合集判定函数"
```

---

### Task 2: `archive_album` 合集分支 + REST/MCP/参数透传

**Files:**
- Modify: `app/archive.py`（`archive_album` 主流程 `archive.py:179-296`、`_write_album_info` `archive.py:156-176`）
- Modify: `app/schemas.py:95-102`（`ArchiveRequest`）
- Modify: `app/main.py:90-99`（`api_album_archive`）
- Modify: `mcp_adapter.py:239-270`（`archive_album` 工具）
- Test: `tests/test_archive_va.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `_write_tags(track_artist=, compilation=)`、`_is_compilation`、`_VA_ARTIST`。
- Produces:
  - `archive_album(..., compilation: bool | None = None)`（`app/archive.py`，其余参数不变）
  - `ArchiveRequest.compilation: Optional[bool] = None`
  - `_write_album_info(album_dir, album, entries, display_title, display_artist, compilation=False)`
  - 合集时：`library_dir = {库根}/群星/{专辑}`，逐曲 ARTIST，跳过 `_save_artist_image`

- [ ] **Step 1: 追加失败测试**

`tests/test_archive_va.py` 追加（import 处加 `import json, os`、`from app.config import settings`）：

```python
def _make_va_manifest(tmp_path):
    """构造 VA 专辑的假 manifest 与下载目录（两个假 .mp3，逐曲艺人不同）。"""
    src = tmp_path / "downloads" / "va_task"
    src.mkdir(parents=True)
    (src / "01 六月的雨.mp3").write_bytes(b"fake1")
    (src / "02 杀破狼.mp3").write_bytes(b"fake2")
    manifest = {
        "album": {"collection_id": "476385671", "title": "Chinese Paladin",
                  "artists": ["Various Artists"], "release_date": "2005-01-21",
                  "meta_source": "itunes"},
        "cover": None,
        "tracks": [
            {"disc": 1, "track": 1, "title": "六月的雨", "artists": ["胡歌"],
             "status": "ok", "file": "01 六月的雨.mp3", "ext": "mp3"},
            {"disc": 1, "track": 2, "title": "杀破狼", "artists": ["JS"],
             "status": "ok", "file": "02 杀破狼.mp3", "ext": "mp3"},
        ],
    }
    mp = src / "manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return str(mp)


@pytest.fixture()
def library_root(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(settings, "library_root", str(root))
    monkeypatch.setattr(settings, "extra_library_roots", {})
    return root


def _read_id3(p):
    from mutagen.id3 import ID3
    return ID3(p)


def test_archive_va_album_to_qunxing(tmp_path, library_root):
    mp = _make_va_manifest(tmp_path)
    res = archive.archive_album(manifest_path=mp)
    album_dir = library_root / "群星" / "Chinese Paladin"
    assert res.library_dir == str(album_dir)
    t1 = _read_id3(album_dir / "01 - 六月的雨.mp3")
    assert str(t1["TPE1"].text[0]) == "胡歌"
    assert str(t1["TPE2"].text[0]) == "群星"
    assert str(t1["TCMP"].text[0]) == "1"
    t2 = _read_id3(album_dir / "02 - 杀破狼.mp3")
    assert str(t2["TPE1"].text[0]) == "JS"
    # 群星目录不写艺人头像
    assert not [p for p in album_dir.parent.iterdir() if p.stem.lower() == "artist"]
    info = (album_dir / "album_info.txt").read_text(encoding="utf-8")
    assert "艺人：群星（合集）" in info
    assert "01. 六月的雨 - 胡歌" in info
    assert "02. 杀破狼 - JS" in info


def test_archive_compilation_override(tmp_path, library_root):
    mp = _make_va_manifest(tmp_path)
    # 显式 compilation=False：按普通专辑归到 Various Artists 目录
    res = archive.archive_album(manifest_path=mp, compilation=False)
    album_dir = library_root / "Various Artists" / "Chinese Paladin"
    assert res.library_dir == str(album_dir)
    t1 = _read_id3(album_dir / "01 - 六月的雨.mp3")
    assert str(t1["TPE1"].text[0]) == "Various Artists"
    assert not t1.getall("TCMP")


def test_archive_normal_album_unaffected(tmp_path, library_root):
    src = tmp_path / "downloads" / "normal_task"
    src.mkdir(parents=True)
    (src / "01 晴天.mp3").write_bytes(b"fake3")
    manifest = {
        "album": {"collection_id": "1", "title": "叶惠美", "artists": ["周杰伦"],
                  "release_date": "2003-07-31", "meta_source": "itunes+netease"},
        "cover": None,
        "tracks": [{"disc": 1, "track": 1, "title": "晴天", "artists": ["周杰伦"],
                    "status": "ok", "file": "01 晴天.mp3", "ext": "mp3"}],
    }
    mp = src / "manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    res = archive.archive_album(manifest_path=str(mp))
    album_dir = library_root / "周杰伦" / "叶惠美"
    assert res.library_dir == str(album_dir)
    t = _read_id3(album_dir / "01 - 晴天.mp3")
    assert str(t["TPE1"].text[0]) == "周杰伦"
    assert not t.getall("TCMP")
    info = (album_dir / "album_info.txt").read_text(encoding="utf-8")
    assert "01. 晴天 - " not in info  # 普通专辑曲目表不附艺人
```

注意：归档成功后 `cleanup_task_dir` 会清理下载产物，这属正常流程（假目录随 tmp_path 一起消失）。若 `settings.cleanup` 配置导致清理报错，测试会暴露——本测试同时回归了这条链路。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_archive_va.py -v`
Expected: FAIL（`archive_album() got an unexpected keyword argument 'compilation'`，且 VA 目录断言失败）

- [ ] **Step 3: 实现改动**

`app/schemas.py` `ArchiveRequest`（95-102 行）`library` 字段后追加：

```python
    compilation: Optional[bool] = Field(default=None, description="合集（Various Artists）标记：None 按 VA 名单自动判定，True/False 强制走/不走合集归档（目录 {库根}/群星/，逐曲艺人写 ARTIST，COMPILATION=1）")
```

`app/main.py` `api_album_archive`（90-99 行）调用加参：

```python
        return archive_svc.archive_album(task_id=req.task_id, manifest_path=req.manifest_path,
                                         overwrite=req.overwrite, album_title=req.album_title,
                                         artist=req.artist, library=req.library,
                                         compilation=req.compilation)
```

`app/archive.py` `archive_album` 签名（179 行）加参：

```python
def archive_album(task_id: str | None = None, manifest_path: str | None = None,
                  overwrite: bool = False, album_title: str | None = None,
                  artist: str | None = None, library: str | None = None,
                  compilation: bool | None = None) -> ArchiveResult:
```

docstring 中「显式 album_title/artist 参数 > manifest display_* > 自动推断 > iTunes 原名。」后追加一行：「compilation：合集标记覆盖（None=VA 名单自动判定）；合集归 {库根}/群星/{专辑}/，逐曲艺人写 ARTIST，ALBUMARTIST=群星，COMPILATION=1。」

主流程（204-205 行）目录解析改为：

```python
    disp_title, disp_artist = _resolve_names(album, ok_entries, album_title, artist)
    is_va = _is_compilation(album, disp_artist, artist, compilation)
    dir_artist = _VA_ARTIST if is_va else disp_artist
    album_dir = root / _safe_name(dir_artist) / _safe_name(disp_title)
```

写 tag 调用（249-251 行）改为：

```python
                    track_artist = (" / ".join(t2s(a) for a in entry.get("artists") or [] if a)
                                    or None) if is_va else None
                    _write_tags(target, entry.get("title") or "", dir_artist, disp_title,
                                (album.get("release_date") or "")[:10],
                                numbers=numbers, cover_bytes=cover_bytes, lyric_text=lyric_text,
                                track_artist=track_artist, compilation=is_va)
```

`_write_album_info` 调用（269 行）改为：

```python
    _write_album_info(album_dir, album, entries, disp_title, dir_artist, compilation=is_va)
```

艺人头像（270-273 行）加合集跳过：

```python
    # 艺人头像（幂等）：取首个带头像的成功条目写入艺人目录，已有 artist.* 则跳过
    # 合集专辑跳过——「群星」不是具体艺人，不挂头像
    if not is_va:
        img_url = next(((e.get("match") or {}).get("artist_img_url") for e in ok_entries
                        if (e.get("match") or {}).get("artist_img_url")), None)
        _save_artist_image(album_dir.parent, img_url)
```

`_write_album_info`（156-176 行）签名加 `compilation: bool = False`，两处行格式：

```python
    artist_line = (f"艺人：{_VA_ARTIST}（合集）" if compilation else
                   f"艺人：{display_artist}" + (f"（iTunes 原名：{orig_artists}）" if itunes_based and orig_artists and orig_artists != display_artist else ""))
```

（把原 `lines = [...]` 里的艺人行替换为 `artist_line` 变量。）

曲目表循环（170-174 行）追加逐曲艺人：

```python
    for e in entries:
        dur = e.get("duration_s")
        dur_s = f" ({int(dur // 60)}:{int(dur % 60):02d})" if dur else ""
        prefix = f"CD{e['disc']} " if len({x['disc'] for x in entries}) > 1 else ""
        va_suffix = ""
        if compilation and e.get("artists"):
            va_suffix = " - " + " / ".join(t2s(a) for a in e["artists"] if a)
        lines.append(f"{prefix}{e['track']:02d}. {t2s(e.get('title'))}{dur_s}{va_suffix}")
```

`mcp_adapter.py` `archive_album`（239 行起）签名加 `compilation: bool | None = None`，docstring 的 Args 加一行、Returns 的目录结构句更新：

```python
        compilation: 合集标记覆盖（可选；None=按 VA 名单自动判定，合集归 {库根}/群星/{专辑}/，
            逐曲艺人写 ARTIST、COMPILATION=1，Navidrome 按合集分组）
```

payload 段加：

```python
    if compilation is not None:
        payload["compilation"] = compilation
```

Returns 段「目录结构为 {库根}/{艺人}/{专辑}/」改为「目录结构为 {库根}/{艺人}/{专辑}/（合集为 {库根}/群星/{专辑}/）」。

- [ ] **Step 4: 运行全部测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass（含 test_cn_meta.py、test_libops.py 回归）

- [ ] **Step 5: Commit**

```bash
git add app/archive.py app/schemas.py app/main.py mcp_adapter.py tests/test_archive_va.py
git commit -m "feat: 合集专辑归档到群星目录（逐曲艺人 ARTIST + COMPILATION 标记 + compilation 覆盖参数）"
```

---

### Task 3: `replace_album_track` 沿用旧 ARTIST/COMPILATION

**Files:**
- Modify: `app/libops.py:94-123`（`_read_tags` 加两键）、`app/libops.py:428-442, 510-511`（`replace_album_track`）
- Test: `tests/test_archive_va.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `_write_tags(track_artist=, compilation=)`。
- Produces:
  - `_read_tags(path)` 返回 dict 新增 `"albumartist"`（`ALBUMARTIST`/`TPE2`）与 `"compilation"`（`COMPILATION`/`TCMP`）两个键
  - `replace_album_track` 替换时 ARTIST/ALBUMARTIST/COMPILATION 沿用旧文件 tag（旧文件缺 tag 时回退 artist 参数）

- [ ] **Step 1: 追加失败测试**

`tests/test_archive_va.py` 追加（import 处加 `from app import libops, download as dl`、`from app.album import quality_tier` 不需要——直接构造 Track）：

```python
def test_replace_track_preserves_va_tags(tmp_path, monkeypatch):
    """合集曲目替换：新文件沿用旧文件的逐曲 ARTIST、ALBUMARTIST=群星 与 COMPILATION。"""
    from app.schemas import Track
    root = tmp_path / "library"
    album_dir = root / "群星" / "仙剑奇侠传 电视原声带"
    album_dir.mkdir(parents=True)
    old = album_dir / "03 - 六月的雨.mp3"
    old.write_bytes(b"old bytes")
    _write_tags(old, "六月的雨", "群星", "仙剑奇侠传 电视原声带",
                numbers={"TRACKNUMBER": "3/13"}, track_artist="胡歌", compilation=True)

    monkeypatch.setattr(settings, "library_root", str(root))
    monkeypatch.setattr(settings, "extra_library_roots", {})
    monkeypatch.setattr(settings, "download_root", str(tmp_path / "downloads"))

    chosen = Track(id="S:1", source="S", title="六月的雨", artists=["胡歌"], album="仙剑奇侠传",
                   ext="mp3", quality="lossless", raw={"identifier": "x"})
    monkeypatch.setattr("app.album.match_track",
                        lambda *a, **kw: {"status": "matched",
                                          "match": {"score": 0.99, "source": "S", "track_id": "1",
                                                    "title": "六月的雨", "artists": ["胡歌"],
                                                    "album": "仙剑奇侠传", "ext": "mp3",
                                                    "quality": "lossless", "quality_tier": 3,
                                                    "artist_img_url": None, "score_detail": None,
                                                    "candidates": 1, "oversized_filtered": 0,
                                                    "oversized_relaxed": False},
                                          "track": chosen})

    def _fake_download(source, songs, save_dir):
        from pathlib import Path as P
        (P(save_dir) / "new.mp3").write_bytes(b"new bytes")
    monkeypatch.setattr(dl, "download_songs", _fake_download)
    monkeypatch.setattr(dl, "_find_downloaded_file", lambda *a, **kw: "new.mp3")

    res = libops.replace_album_track(None, "群星", "仙剑奇侠传 电视原声带", 3, force=True)
    assert res["action"] == "replaced"
    tags = _read_id3(album_dir / "03 - 六月的雨.mp3")
    assert str(tags["TPE1"].text[0]) == "胡歌"
    assert str(tags["TPE2"].text[0]) == "群星"
    assert str(tags["TCMP"].text[0]) == "1"
    assert str(tags["TRCK"].text[0]) == "3/13"
```

（旧假 mp3 的 tier 为 1，chosen `quality="lossless"` tier 3 > 1，无需 force 也会替换；`force=True` 双保险。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_archive_va.py::test_replace_track_preserves_va_tags -v`
Expected: FAIL（当前实现 ARTIST 会被写成"群星"、TCMP 丢失）

- [ ] **Step 3: 实现改动**

`app/libops.py` `_read_tags`（115-120 行区域）加两键：

```python
        out["title"] = _get("TITLE", "TIT2")
        out["artist"] = _get("ARTIST", "TPE1")
        out["albumartist"] = _get("ALBUMARTIST", "TPE2")
        out["album"] = _get("ALBUM", "TALB")
        out["date"] = _get("DATE", "TDRC")
        out["tracknumber"] = _get("TRACKNUMBER", "TRCK")
        out["discnumber"] = _get("DISCNUMBER", "TPOS")
        out["compilation"] = _get("COMPILATION", "TCMP")
        return out
```

`replace_album_track` 两处改动。匹配期望艺人（441 行）改用旧 tag：

```python
    expected = AlbumTrack(disc=_num(old_tags.get("discnumber"), 1),
                          track=_num(old_tags.get("tracknumber"), 0),
                          title=title, artists=[t2s(old_tags.get("artist") or artist)],
                          duration_s=old_tags.get("duration_s"))
```

写 tag 调用（510-511 行）改为沿用旧 tag：

```python
            _write_tags(new_file, title,
                        t2s(old_tags.get("albumartist") or artist), t2s(album),
                        old_tags.get("date") or "",
                        numbers=numbers, cover_bytes=cover_bytes, lyric_text=lyric_text,
                        track_artist=old_tags.get("artist") or None,
                        compilation="1" in (old_tags.get("compilation") or ""))
```

同时把 `replace_album_track` docstring 里「序号/专辑/艺人/日期沿用旧 tag」保持不动（实现现在与之一致了）。

- [ ] **Step 4: 运行全部测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass

- [ ] **Step 5: Commit**

```bash
git add app/libops.py tests/test_archive_va.py
git commit -m "fix: 专辑曲目替换沿用旧文件 ARTIST/ALBUMARTIST/COMPILATION（兼容合集）"
```

---

### Task 4: 文档与 ROADMAP 收尾

**Files:**
- Modify: `docs/API.md`（`POST /api/v1/albums/archive` 节，约 300 行附近）
- Modify: `docs/MCP.md`（`archive_album` 节，约 115 行）
- Modify: `ROADMAP.md`（2e 条目后新增 2f）
- Test: 无新测试；全量回归 + 冒烟

**Interfaces:**
- Consumes: Task 1-3 最终行为。
- Produces: 文档与实际行为一致。

- [ ] **Step 1: 更新 `docs/API.md`**

先 `grep -n "albums/archive" docs/API.md` 定位该节。在请求体说明中给 `compilation` 加参数说明，并在该节末尾追加一段：

```markdown
> **合集（Various Artists）专辑**：专辑艺人命中 VA 名单（various artists / va / 群星 / 华语群星 / 合辑，繁简大小写不敏感）或显式传 `compilation: true` 时，归档到 `{库根}/群星/{专辑}/`：逐曲艺人写 ARTIST tag，ALBUMARTIST=群星，COMPILATION=1（Navidrome 按合集分组），不写艺人头像，`album_info.txt` 曲目表附逐曲艺人。显式传 `artist` 而未传 `compilation` 时按普通专辑处理；`compilation: false` 可强制普通归档。老 manifest 重跑 `overwrite: true` 归档即可迁入群星目录（旧位置目录用 `cleanup_library` 按需清理）。
```

- [ ] **Step 2: 更新 `docs/MCP.md`**

`archive_album` 节（约 115 行）签名行改为 `archive_album(task_id?, manifest_path?, overwrite?, album_title?, artist?, library?, compilation?)`，段落末尾追加：「合集专辑（Various Artists/群星）自动归档到 `{库根}/群星/{专辑}/`，逐曲艺人写 ARTIST、COMPILATION=1；`compilation` 参数可强制覆盖自动判定。」

- [ ] **Step 3: 更新 `ROADMAP.md`**

在 2e 条目（媒体库生命周期管理四场景）之后插入：

```markdown
2f. **合集（Various Artists）专辑归档** ✅ 已完成（2026-09-01）
   - 背景：合集/原声带（如《仙剑奇侠传》电视原声带）不是单一艺人专辑，按 `{艺人}/{专辑}/` 归档会让目录名不一致（Various Artists/群星混杂）且 tag 丢失逐曲艺人
   - 已实现：VA 名单（various artists/va/群星/华语群星/合辑）自动判定 + `compilation` 显式覆盖参数（REST + MCP）；合集归档到 `{库根}/群星/{专辑}/`，逐曲艺人写 ARTIST、ALBUMARTIST=群星、COMPILATION=1（FLAC/MP3-TCMP，Navidrome 按合集分组），不写艺人头像，`album_info.txt` 曲目表附逐曲艺人；`replace_album_track` 顺带修正为沿用旧文件 ARTIST/ALBUMARTIST/COMPILATION（与其 docstring 对齐）
   - 兼容性：老 manifest 重跑 overwrite 归档即迁入群星目录；显式 artist 参数优先于名单判定
```

- [ ] **Step 4: 全量回归 + 冒烟**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全 pass

冒烟（可选）：`.venv/bin/uvicorn app.main:app --port 18765` 启动后，对任一 VA manifest（可用 Task 2 测试里的构造方式临时生成）调 `POST /api/v1/albums/archive` 验证 `library_dir` 落在 `群星/` 下。8765 端口被在运行的实例占用，冒烟统一用 18765。

- [ ] **Step 5: Commit**

```bash
git add docs/API.md docs/MCP.md ROADMAP.md docs/superpowers/specs/2026-09-01-va-album-archive-design.md docs/superpowers/plans/2026-09-01-va-album-archive.md
git commit -m "docs: 合集专辑归档的接口说明、ROADMAP 2f 与设计/计划文档"
```

---

## Self-Review 记录

- Spec 覆盖：VA 名单判定（Task 1）、优先级细则（Task 1 测试 + Task 2 覆盖用例）、目录/tag/头像/album_info 合集行为（Task 2）、`ArchiveRequest.compilation` 与 MCP 透传（Task 2）、`replace_album_track` 沿用旧 tag（Task 3）、文档与 ROADMAP（Task 4）。
- 类型一致性：`_write_tags` 新签名在 Task 2/3 的所有调用点一致；`_is_compilation` 参数顺序 `(album, display_artist, explicit_artist, compilation)` 定义与调用一致；`_read_tags` 新键名 `albumartist`/`compilation` 在 Task 3 使用一致。
- 已知取舍：FLAC 分支无真实文件测试手段（mutagen 不能从零造 FLAC），由 MP3 路径等价覆盖；TCMP 自定义帧注册在 `if compilation:` 块内，先写后读场景注册即生效（测试均先写后读）。
- `cleanup_task_dir` 在归档成功链路中会被调用，E2E 测试（tmp_path）同时回归了这条链。
