# 媒体库生命周期四场景实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MediaMusicService 增加四个媒体库运维场景：专辑下载复用 singles 库、指定曲目高规格替换、库内清理（空目录一并清除）、单曲专辑迁移到 singles 库。

**Architecture:** 新增 `app/libops.py` 承载库运维逻辑；场景 1 对 `album.py`/`archive.py` 做小改动接线；REST 新增 3 个端点、MCP 新增 3 个工具。所有删除/移动只作用于白名单库根之内。

**Tech Stack:** Python 3.11 / FastAPI / pydantic v2 / mutagen / pytest（新增 dev 依赖，跑在 uv venv 中）

**Spec:** `docs/superpowers/specs/2026-08-13-library-lifecycle-design.md`

## Global Constraints

- 库名一律走 `app/libraries.py::resolve_library_root` 白名单解析，不接受裸路径。
- 所有删除/移动前必须 `_under_root`（resolve + `is_relative_to`）校验，与 `app/cleanup.py` 同一安全红线。
- 硬链接文件改 tag 前必须断链（复用 `archive._break_link_if_needed`）。
- 音质分档口径统一：无损 ext → 3；bitrate ≥ 320k → 2；其他 → 1（与 `album.quality_tier` 一致）。
- 命名/繁简/`_safe_name` 一律复用 `app/album.py` 的 `_sim`/`_normalize`/`_artist_sim`/`t2s`/`_safe_name`。
- 本项目无 git 操作：实现过程中**不执行任何 git commit/add/push**。
- 文档（API.md/MCP.md/ROADMAP.md）用简体中文，风格与现有条目一致。

## 环境准备（所有任务前置，只做一次）

```bash
cd /home/yangds/github/media-music-service
uv venv .venv
uv pip install -p .venv/bin/python -r requirements.txt pytest
.venv/bin/python -c "import app.main; print('import ok')"
```

预期：venv 创建成功、依赖装好、`import app.main` 无报错。
（libops 会 import `app.album` → `app.search` → `musicdl`，所以必须装全量依赖。）

---

### Task 1: `app/libops.py` 基础件 + 单元测试

**Files:**
- Create: `app/libops.py`
- Create: `tests/test_libops.py`

**Interfaces:**
- Consumes: `app.album` 的 `_sim`/`_artist_sim`/`_safe_name`/`t2s`/`quality_tier`；`app.download._AUDIO_EXTS`；`app.libraries.resolve_library_root`；`app.config.settings`
- Produces（后续任务依赖这些精确签名）:
  - `find_in_singles(title: str, artists: list[str], album_title: str, duration_s: float | None, singles_root: str) -> Path | None`
  - `remove_reused_single(path_str: str, singles_root: str) -> None`
  - `file_quality_tier(path: Path) -> int`
  - `find_album_track_files(album_dir: Path, tracks: list) -> list[Path]`
  - `_read_tags(path: Path) -> dict`（键：duration_s/bitrate/title/artist/album/date/tracknumber/discnumber）
  - `_under_root(p: Path, root: Path) -> bool`、`_rmdir_if_empty(d: Path, root: Path) -> bool`、`_audio_files(d: Path) -> list[Path]`、`_strip_nn(stem: str) -> str`
  - 模块常量 `_TAGGABLE_EXTS = {"flac", "mp3"}`、`_LOSSLESS_EXTS`

- [ ] **Step 1: 写失败测试** `tests/test_libops.py`

```python
"""libops 基础件单元测试：匹配、音质分档、曲目定位、安全边界。"""
from pathlib import Path

import pytest

from app.config import settings
from app import libops


@pytest.fixture()
def singles_root(tmp_path, monkeypatch):
    root = tmp_path / "singles"
    root.mkdir()
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(root)})
    return root


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake")
    return p


def test_find_in_singles_hit(singles_root):
    f = _touch(singles_root / "周杰伦" / "蜗牛.flac")
    assert libops.find_in_singles("蜗牛", ["周杰伦"], "范特西", None, str(singles_root)) == f


def test_find_in_singles_wrong_artist(singles_root):
    _touch(singles_root / "周杰伦" / "蜗牛.flac")
    assert libops.find_in_singles("蜗牛", ["林俊杰"], "范特西", None, str(singles_root)) is None


def test_find_in_singles_wrong_title(singles_root):
    _touch(singles_root / "周杰伦" / "蜗牛.flac")
    assert libops.find_in_singles("简单爱", ["周杰伦"], "范特西", None, str(singles_root)) is None


def test_file_quality_tier():
    assert libops.file_quality_tier(Path("a.flac")) == 3
    assert libops.file_quality_tier(Path("a.wav")) == 3
    assert libops.file_quality_tier(Path("a.mp3")) == 1  # 伪文件读不出 bitrate


def test_find_album_track_files(tmp_path):
    d = tmp_path / "艺人" / "专辑"
    _touch(d / "01 - 爱在西元前.flac")
    _touch(d / "02 - 简单爱.mp3")
    _touch(d / "CD1" / "03 - 忍者.flac")
    by_no = libops.find_album_track_files(d, [2])
    assert [p.name for p in by_no] == ["02 - 简单爱.mp3"]
    by_title = libops.find_album_track_files(d, ["爱在西元前"])
    assert [p.name for p in by_title] == ["01 - 爱在西元前.flac"]
    by_cd = libops.find_album_track_files(d, ["03"])
    assert [p.name for p in by_cd] == ["03 - 忍者.flac"]
    assert libops.find_album_track_files(d, ["不存在的歌xyz"]) == []


def test_remove_reused_single(singles_root):
    f = _touch(singles_root / "周杰伦" / "蜗牛.flac")
    _touch(singles_root / "周杰伦" / "蜗牛.lrc")
    libops.remove_reused_single(str(f), str(singles_root))
    assert not f.exists()
    assert not (singles_root / "周杰伦" / "蜗牛.lrc").exists()
    assert not (singles_root / "周杰伦").exists()  # 空艺人目录一并清理


def test_remove_reused_single_outside_root_noop(singles_root, tmp_path):
    outside = _touch(tmp_path / "elsewhere" / "歌.flac")
    libops.remove_reused_single(str(outside), str(singles_root))
    assert outside.exists()  # 库外文件绝不动
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_libops.py -v`
Expected: FAIL（`ModuleNotFoundError: app.libops`）

- [ ] **Step 3: 实现 `app/libops.py`**

```python
"""媒体库运维：singles 复用查找、指定曲目替换、库内清理、单曲专辑迁移。

安全红线（与 cleanup.py 同口径）：所有删除/移动只作用于白名单库根之内
（resolve_library_root 解析 + is_relative_to 校验），不碰库外任何路径。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .album import _artist_sim, _safe_name, _sim, quality_tier, t2s
from .config import settings
from .download import _AUDIO_EXTS
from .libraries import resolve_library_root

_LOSSLESS_EXTS = {"flac", "ape", "wav", "alac", "tak", "tta", "dsd", "dff", "dsf"}
_TAGGABLE_EXTS = {"flac", "mp3"}
_NN_PREFIX_RE = re.compile(r"^\d+\s*-\s*")


def _under_root(p: Path, root: Path) -> bool:
    """路径解析后必须严格位于库根之内（防越界删除/移动）。"""
    try:
        r = root.resolve()
        resolved = p.resolve()
        return resolved != r and resolved.is_relative_to(r)
    except Exception:
        return False


def _audio_files(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_EXTS)
    except Exception:
        return []


def _strip_nn(stem: str) -> str:
    """去掉文件名的 'NN - ' 序号前缀。"""
    return _NN_PREFIX_RE.sub("", stem).strip()


def _read_tags(path: Path) -> dict[str, Any]:
    """读取音频 tag 与时长/码率（mutagen，尽力而为）；失败返回空 dict。"""
    try:
        import mutagen
        f = mutagen.File(path)
        if f is None:
            return {}
        info = getattr(f, "info", None)
        out: dict[str, Any] = {"duration_s": getattr(info, "length", None),
                               "bitrate": getattr(info, "bitrate", None)}

        def _get(*keys: str) -> str:
            for k in keys:
                try:
                    v = f.get(k)
                except Exception:
                    v = None
                if v:
                    return str(v[0]) if isinstance(v, (list, tuple)) else str(v)
            return ""

        out["title"] = _get("TITLE", "TIT2")
        out["artist"] = _get("ARTIST", "TPE1")
        out["album"] = _get("ALBUM", "TALB")
        out["date"] = _get("DATE", "TDRC")
        out["tracknumber"] = _get("TRACKNUMBER", "TRCK")
        out["discnumber"] = _get("DISCNUMBER", "TPOS")
        return out
    except Exception:
        return {}


def file_quality_tier(path: Path) -> int:
    """库内文件音质分档（与 album.quality_tier 同口径）：无损 ext 3 / ≥320k 2 / 其他 1。"""
    if path.suffix.lstrip(".").lower() in _LOSSLESS_EXTS:
        return 3
    br = _read_tags(path).get("bitrate")
    try:
        return 2 if br and int(br) >= 320000 else 1
    except (TypeError, ValueError):
        return 1


def _rmdir_if_empty(d: Path, root: Path) -> bool:
    """目录为空则删除（必须在库根之内）；返回是否删除。"""
    try:
        if d.is_dir() and _under_root(d, root) and not any(d.iterdir()):
            d.rmdir()
            return True
    except Exception:
        pass
    return False


def find_in_singles(title: str, artists: list[str], album_title: str,
                    duration_s: float | None, singles_root: str) -> Path | None:
    """在 singles 库 {root}/{艺人}/{曲名.ext} 中查找同专辑曲目，命中返回路径否则 None。

    保守匹配（宁可重下也不错拿）：曲名与文件名 _sim >= 0.85；有 tag TITLE 时同样要求 >= 0.85；
    tag ALBUM 存在时与专辑名 _sim >= 0.6；两侧都有时长时差值 <= 15s。
    """
    root = Path(singles_root)
    if not root.is_dir():
        return None
    for artist_dir in sorted(root.iterdir()):
        if not artist_dir.is_dir():
            continue
        if _artist_sim(artists, [artist_dir.name]) < 0.6:
            continue
        for f in sorted(artist_dir.iterdir()):
            if not (f.is_file() and f.suffix.lower() in _AUDIO_EXTS):
                continue
            if _sim(title, f.stem) < 0.85:
                continue
            tags = _read_tags(f)
            if tags.get("title") and _sim(title, tags["title"]) < 0.85:
                continue
            if tags.get("album") and _sim(album_title, tags["album"]) < 0.6:
                continue
            if (duration_s and tags.get("duration_s")
                    and abs(float(tags["duration_s"]) - float(duration_s)) > 15):
                continue
            return f
    return None


def remove_reused_single(path_str: str, singles_root: str) -> None:
    """专辑归档成功后的迁移语义：删除 singles 库中的源文件与同名 .lrc，并清理空艺人目录。"""
    root = Path(singles_root)
    p = Path(path_str)
    if not _under_root(p, root):
        return
    for f in (p, p.with_suffix(".lrc")):
        try:
            if f.is_file():
                f.unlink()
        except Exception:
            pass
    _rmdir_if_empty(p.parent, root)


def find_album_track_files(album_dir: Path, tracks: list[Any]) -> list[Path]:
    """按曲目序号（NN 前缀，含 CDx/ 子目录）或曲名相似度定位专辑内音频文件。

    tracks 元素为 int/数字字符串（序号）或曲名（_sim >= 0.7 取最高分）；未命中的元素静默跳过。
    """
    files = _audio_files(album_dir)
    out: list[Path] = []
    for t in tracks:
        s = str(t).strip()
        hit: Path | None = None
        if s.isdigit():
            hit = next((f for f in files if re.match(rf"^{int(s):02d}\s*-\s*", f.name)), None)
        else:
            scored = sorted(((_sim(s, _strip_nn(f.stem)), f) for f in files),
                            key=lambda x: x[0], reverse=True)
            if scored and scored[0][0] >= 0.7:
                hit = scored[0][1]
        if hit and hit not in out:
            out.append(hit)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_libops.py -v`
Expected: 8 passed

---

### Task 2: 场景 1 接线——专辑下载复用 singles 库 + 归档迁移

**Files:**
- Modify: `app/album.py`（`_run_album` 复用逻辑；新增 `import os, shutil`）
- Modify: `app/archive.py`（`archive_album` 末尾迁移删除）
- Test: `tests/test_libops.py`（追加接线级测试，用假文件 + monkeypatch 模拟）

**Interfaces:**
- Consumes: Task 1 的 `find_in_singles`/`file_quality_tier`/`remove_reused_single`
- Produces: manifest 的 `tracks[].match` 新增可选字段 `source: "singles"` 与 `reused_from: <绝对路径>`；`album.py` 仍只通过 `libops` 的公开函数交互（在 `_run_album` 内延迟 import，避免模块级循环）

- [ ] **Step 1: 写失败测试（追加到 tests/test_libops.py）**

```python
def test_archive_album_migrates_reused_single(tmp_path, monkeypatch):
    """归档成功后 singles 源文件被删除（迁移语义），空艺人目录清理。"""
    import json
    from app import archive as archive_svc

    lib = tmp_path / "library"
    singles = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(lib))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(singles)})
    monkeypatch.setattr(settings.cleanup, "after_archive", False)

    # 用 .wav 规避 _write_tags（假文件过不了 mutagen；wav 走 tag_unsupported 分支，仍在迁移白名单内）
    src_single = _touch(singles / "周杰伦" / "蜗牛.wav")
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    # 模拟 _run_album 的复用产物：save_dir 里是 singles 文件的硬链接
    os.link(src_single, dl_dir / "01 蜗牛.wav")
    manifest = {
        "task_id": "t1", "created_at": 0,
        "album": {"collection_id": "c", "title": "范特西", "artists": ["周杰伦"],
                  "release_date": "2001-09-14", "track_count": 1,
                  "cover_url": None, "genre": None, "meta_source": "itunes"},
        "cover": None,
        "tracks": [{"disc": 1, "track": 1, "title": "蜗牛", "artists": ["周杰伦"],
                    "duration_s": None, "status": "ok", "ext": "wav",
                    "match": {"source": "singles", "reused_from": str(src_single),
                              "title": "蜗牛", "artists": ["周杰伦"], "album": "范特西",
                              "ext": "wav", "quality_tier": 3},
                    "file": "01 蜗牛.wav", "size_bytes": 4, "error": None}],
        "summary": {"total": 1, "ok": 1, "unmatched": 0, "failed": 0},
    }
    (dl_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    res = archive_svc.archive_album(manifest_path=str(dl_dir / "manifest.json"))
    assert res.status == "success"
    assert (lib / "周杰伦" / "范特西" / "01 - 蜗牛.wav").exists()
    assert not src_single.exists()            # singles 源已迁移
    assert not (singles / "周杰伦").exists()  # 空艺人目录已清
```

注意文件顶部需 `import os`。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_libops.py::test_archive_album_migrates_reused_single -v`
Expected: FAIL（singles 源文件仍在——迁移逻辑未实现）

- [ ] **Step 3: 实现接线**

`app/album.py`：

1. 顶部 import 区加 `import os` 和 `import shutil`（当前没有）。
2. `_run_album` 函数体开头（`task.status = TaskStatus.RUNNING` 之后）加：

```python
    # singles 库复用：同专辑曲目已存在时不重复下载，归档时迁移进专辑库
    singles_root: str | None = None
    try:
        from .libraries import resolve_library_root
        singles_root = resolve_library_root("singles")
    except (RuntimeError, LookupError):
        pass
```

3. `_run_album` 匹配循环里，`entry = {...}` 之后、`r = match_track(...)` 之前插入（命中则 continue）：

```python
        if singles_root:
            from . import libops  # 延迟 import 防模块级循环（libops 依赖 album）
            reused = libops.find_in_singles(expected.title, expected.artists, album.title,
                                            expected.duration_s, singles_root)
            if reused:
                ext = reused.suffix.lstrip(".")
                fname = _track_filename(expected, multi_disc, ext)
                dst = Path(save_dir) / fname
                try:
                    os.link(reused, dst)
                except OSError:
                    shutil.copy2(reused, dst)
                lrc = reused.with_suffix(".lrc")
                if lrc.exists():
                    shutil.copy2(lrc, dst.with_suffix(".lrc"))
                entry.update(status="ok", file=fname, ext=ext,
                             size_bytes=dst.stat().st_size if dst.exists() else None,
                             match={"source": "singles", "reused_from": str(reused),
                                    "title": expected.title, "artists": expected.artists,
                                    "album": album.title, "ext": ext,
                                    "quality_tier": libops.file_quality_tier(reused)})
                task.completed += 1
                task.results.append({"disc": expected.disc, "track": expected.track,
                                     "title": expected.title, "file": fname, "source": "singles"})
                entries.append(entry)
                dl.save_task(task)
                continue
```

`app/archive.py` `archive_album` 中，`status, summary, errors = _summarize(results)` 之后、`from .cleanup import cleanup_task_dir` 之前插入：

```python
    # singles 复用迁移：成功入库后删除 singles 源文件与同名 .lrc，并清理空艺人目录
    try:
        singles_root = resolve_library_root("singles")
    except (RuntimeError, LookupError):
        singles_root = None
    if singles_root:
        from .libops import remove_reused_single
        for e, r in zip(ok_entries, results):
            src = (e.get("match") or {}).get("reused_from")
            if src and r.action in ("linked", "copied", "skipped", "tag_unsupported"):
                remove_reused_single(src, singles_root)
```

- [ ] **Step 4: 运行全部测试**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全部通过（含新测试）

---

### Task 3: 场景 3——cleanup_library + REST + MCP

**Files:**
- Modify: `app/libops.py`（追加 `cleanup_library`）
- Modify: `app/schemas.py`（追加 `CleanupLibraryRequest`）
- Modify: `app/main.py`（追加端点）
- Modify: `mcp_adapter.py`（追加 `cleanup_library` 工具）
- Test: `tests/test_libops.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `find_album_track_files`/`_audio_files`/`_rmdir_if_empty`/`_safe_name`/`t2s`
- Produces: `cleanup_library(library: str | None, artist: str, album: str | None = None, tracks: list | None = None, dry_run: bool = False) -> dict`（返回 `{status, dry_run, deleted_files, removed_dirs, errors}`）；REST `POST /api/v1/library/cleanup`；MCP `cleanup_library(artist, album=None, tracks=None, library=None, dry_run=False)`

- [ ] **Step 1: 写失败测试（追加）**

```python
def _make_album(root: Path, tracks=("01 - a.wav", "02 - b.wav")) -> Path:
    d = root / "艺人" / "专辑"
    for name in tracks:
        _touch(d / name)
    _touch(d / "cover.jpg")
    _touch(d / "album_info.txt")
    _touch(d / "lyrics" / "01 - a.lrc")
    return d


def test_cleanup_library_track_then_prune(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    album_dir = _make_album(root)
    r = libops.cleanup_library("t", "艺人", "专辑", tracks=[1])
    assert not (album_dir / "01 - a.wav").exists()
    assert not (album_dir / "lyrics" / "01 - a.lrc").exists()
    assert album_dir.exists()  # 还有残留曲目，目录保留
    r = libops.cleanup_library("t", "艺人", "专辑", tracks=[2])
    assert not album_dir.exists()          # 无音频残留，整目录清
    assert not (root / "艺人").exists()    # 空艺人目录清
    assert str(album_dir) in r["removed_dirs"]


def test_cleanup_library_dry_run(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    album_dir = _make_album(root)
    r = libops.cleanup_library("t", "艺人", "专辑", tracks=[1], dry_run=True)
    assert (album_dir / "01 - a.wav").exists()  # dry_run 不实际删除
    assert r["deleted_files"]  # 但报告了将删的项


def test_cleanup_library_whole_album_and_artist(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    album_dir = _make_album(root)
    libops.cleanup_library("t", "艺人", "专辑")
    assert not album_dir.exists()
    assert not (root / "艺人").exists()


def test_cleanup_library_missing_artist(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    with pytest.raises(LookupError):
        libops.cleanup_library("t", "不存在", "专辑")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_libops.py -k cleanup_library -v`
Expected: FAIL（`AttributeError: ... no attribute 'cleanup_library'`）

- [ ] **Step 3: 实现**

`app/libops.py` 追加：

```python
def cleanup_library(library: str | None, artist: str, album: str | None = None,
                    tracks: list[Any] | None = None, dry_run: bool = False) -> dict:
    """清理媒体库中的专辑/曲目文件，空目录一并清理（不留空目录）。

    粒度：tracks 指定曲目 > album 整专辑 > artist 整艺人。
    删除曲目后自底向上：空 CDx/ → 无音频残留的专辑目录（连同 cover/album_info/lyrics）→ 空艺人目录。
    dry_run=True 只报告不删除。
    """
    root = Path(resolve_library_root(library))
    artist_dir = root / _safe_name(t2s(artist))
    if not artist_dir.is_dir():
        raise LookupError(f"艺人目录不存在: {artist_dir}")
    deleted_files: list[str] = []
    removed_dirs: list[str] = []
    result = {"status": "success", "dry_run": dry_run,
              "deleted_files": deleted_files, "removed_dirs": removed_dirs, "errors": []}

    if not album:
        if not dry_run:
            shutil.rmtree(artist_dir)
        removed_dirs.append(str(artist_dir))
        return result

    album_dir = artist_dir / _safe_name(t2s(album))
    if not album_dir.is_dir():
        raise LookupError(f"专辑目录不存在: {album_dir}")

    if not tracks:
        if not dry_run:
            shutil.rmtree(album_dir)
            if _rmdir_if_empty(artist_dir, root):
                removed_dirs.append(str(artist_dir))
        removed_dirs.insert(0, str(album_dir))
        return result

    targets = find_album_track_files(album_dir, tracks)
    if not targets:
        raise LookupError(f"专辑内未找到匹配曲目: {tracks}")
    for f in targets:
        deleted_files.append(str(f))
        for lrc in (album_dir / "lyrics" / f"{f.stem}.lrc", f.with_suffix(".lrc")):
            if lrc.is_file() and str(lrc) not in deleted_files:
                deleted_files.append(str(lrc))
    if not dry_run:
        for f in targets:
            try:
                f.unlink()
            except Exception:
                pass
            for lrc in (album_dir / "lyrics" / f"{f.stem}.lrc", f.with_suffix(".lrc")):
                try:
                    if lrc.is_file():
                        lrc.unlink()
                except Exception:
                    pass
        for cd in sorted(album_dir.iterdir()):
            if cd.is_dir() and re.fullmatch(r"CD\d+", cd.name, re.IGNORECASE):
                _rmdir_if_empty(cd, root)
    if not [f for f in _audio_files(album_dir) if f not in targets]:
        # 专辑已无音频残留：整目录删（含 cover/album_info/lyrics），再清空艺人目录
        if not dry_run:
            shutil.rmtree(album_dir, ignore_errors=True)
            if _rmdir_if_empty(artist_dir, root):
                removed_dirs.append(str(artist_dir))
        removed_dirs.insert(0, str(album_dir))
    return result
```

`app/schemas.py` 追加：

```python
class CleanupLibraryRequest(BaseModel):
    """库内清理请求：tracks 指定曲目 > album 整专辑 > artist 整艺人；空目录一并清理。"""
    library: Optional[str] = Field(default=None, description="库名（见 GET /api/v1/libraries）；留空用默认库")
    artist: str = Field(description="艺人名（对应库内一级目录）")
    album: Optional[str] = Field(default=None, description="专辑名（对应库内二级目录）；留空则清理整个艺人目录")
    tracks: Optional[list[Any]] = Field(default=None, description="要清理的曲目：序号（如 3）或曲名；留空则清理整个专辑")
    dry_run: bool = Field(default=False, description="只报告将删除的项，不实际删除")


class MigrateSinglesRequest(BaseModel):
    """单曲专辑迁移请求：把只有一个音频文件的专辑目录迁移到 singles 库。"""
    library: Optional[str] = Field(default=None, description="源库名；留空用默认库")
    target_library: str = Field(default="singles", description="目标库名（默认 singles）")
    artist: Optional[str] = Field(default=None, description="限定单个艺人；留空扫描整个源库")
    dry_run: bool = Field(default=False, description="只报告将迁移的项，不实际迁移")


class ReplaceTrackRequest(BaseModel):
    """专辑指定曲目重搜替换请求：新候选音质更高（或 force）才替换。"""
    library: Optional[str] = Field(default=None, description="库名；留空用默认库")
    artist: str = Field(description="艺人名（对应库内一级目录）")
    album: str = Field(description="专辑名（对应库内二级目录）")
    track: Any = Field(description="曲目序号（如 3）或曲名")
    sources: Optional[list[str]] = Field(default=None, description="参与搜索的源，留空用默认五源")
    force: bool = Field(default=False, description="新候选音质不高于现有版本也强制替换")
    max_size_mb: Optional[float] = Field(default=None, description="单文件体积上限（MB），>0 优先于配置，0/空不限")
```

`app/main.py`：import 区加 `from . import libops`，schemas import 加 `CleanupLibraryRequest, MigrateSinglesRequest, ReplaceTrackRequest`，追加端点：

```python
@app.post("/api/v1/library/cleanup", dependencies=[Depends(auth)])
def api_library_cleanup(req: CleanupLibraryRequest) -> dict:
    try:
        return libops.cleanup_library(library=req.library, artist=req.artist, album=req.album,
                                      tracks=req.tracks, dry_run=req.dry_run)
    except (ValueError, LookupError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`mcp_adapter.py` 追加（放在 `archive_tracks` 之后）：

```python
@mcp.tool()
def cleanup_library(artist: str, album: str | None = None, tracks: list[str] | None = None,
                    library: str | None = None, dry_run: bool = False) -> dict:
    """清理媒体库中的专辑或曲目文件；存放文件的目录变空时一并清理（不留空目录）。

    Args:
        artist: 艺人名（库内一级目录）
        album: 专辑名（可选；留空则删除整个艺人目录）
        tracks: 要删除的曲目（可选，序号如 "3" 或曲名；留空则删除整个专辑）
        library: 库名（可选，见 list_libraries；留空用默认库）
        dry_run: True 时只报告将删除的项，不实际删除（建议先跑一遍确认范围）
    Returns:
        deleted_files（已删文件）、removed_dirs（已清目录）、errors。
    """
    payload: dict[str, Any] = {"artist": artist, "dry_run": dry_run}
    if album:
        payload["album"] = album
    if tracks:
        payload["tracks"] = tracks
    if library:
        payload["library"] = library
    with _client() as c:
        r = c.post("/api/v1/library/cleanup", json=payload)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: 运行测试 + 导入自检**

Run: `.venv/bin/python -m pytest tests/ -v && .venv/bin/python -c "import app.main, mcp_adapter; print('import ok')"`
Expected: 全部通过 + import ok

---

### Task 4: 场景 4——migrate_singles + `_write_tags` strip_numbers + REST + MCP

**Files:**
- Modify: `app/archive.py`（`_write_tags` 加 `strip_numbers: bool = False` 参数）
- Modify: `app/libops.py`（追加 `migrate_singles`）
- Modify: `app/main.py`（追加端点）
- Modify: `mcp_adapter.py`（追加 `migrate_singles` 工具）
- Test: `tests/test_libops.py`（追加）

**Interfaces:**
- Consumes: Task 1 基础件；`archive._write_tags`（新签名加 `strip_numbers`）、`archive._break_link_if_needed`
- Produces: `migrate_singles(library: str | None = None, target_library: str = "singles", artist: str | None = None, dry_run: bool = False) -> dict`（返回 `{status, dry_run, migrated, skipped, errors}`）；REST `POST /api/v1/library/migrate_singles`；MCP `migrate_singles(...)`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_migrate_singles(tmp_path, monkeypatch):
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "周杰伦" / "范特西 - Single" / "01 - 蜗牛.wav")   # 单曲专辑（wav 跳过写 tag）
    _touch(src / "周杰伦" / "范特西 - Single" / "cover.jpg")
    _touch(src / "周杰伦" / "范特西" / "01 - 爱在西元前.wav")     # 正常专辑（2 首，不动）
    _touch(src / "周杰伦" / "范特西" / "02 - 简单爱.wav")
    r = libops.migrate_singles()
    assert r["status"] == "success"
    assert (dst / "周杰伦" / "蜗牛.wav").exists()                # 已迁移
    assert not (src / "周杰伦" / "范特西 - Single").exists()     # 原专辑目录已清
    assert (src / "周杰伦" / "范特西").exists()                  # 正常专辑不动
    assert (src / "周杰伦").exists()                             # 艺人目录非空保留


def test_migrate_singles_existing_target_skipped(tmp_path, monkeypatch):
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "周杰伦" / "范特西 - Single" / "01 - 蜗牛.wav")
    _touch(dst / "周杰伦" / "蜗牛.wav")
    r = libops.migrate_singles()
    assert r["skipped"] and not r["migrated"]
    assert (src / "周杰伦" / "范特西 - Single" / "01 - 蜗牛.wav").exists()


def test_migrate_singles_dry_run(tmp_path, monkeypatch):
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "周杰伦" / "范特西 - Single" / "01 - 蜗牛.wav")
    r = libops.migrate_singles(dry_run=True)
    assert r["migrated"]  # 报告了
    assert not (dst / "周杰伦" / "蜗牛.wav").exists()  # 但未实际迁移
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_libops.py -k migrate_singles -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`app/archive.py` `_write_tags` 签名与两处分支修改（docstring 同步更新）：

```python
def _write_tags(path: Path, title: str, artist: str, album_title: str, date: str = "",
                numbers: dict[str, str] | None = None,
                cover_bytes: bytes | None = None, lyric_text: str | None = None,
                strip_numbers: bool = False) -> None:
    """按库约定重写 tag 并嵌封面/歌词（仅 flac/mp3）。artist/album_title 为解析后的显示名。

    numbers 为序号类 tag（TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL），专辑归档传入，
    单曲归档传 None（不写序号）；strip_numbers=True 时显式清除已有序号类 tag（单曲迁移用）；
    date 为空则不写 DATE。
    """
```

flac 分支在 `for k, v in numbers.items():` 之前加：

```python
        if strip_numbers:
            for k in ("TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL"):
                if k in audio:
                    del audio[k]
```

mp3 分支在 `if numbers.get("TRACKNUMBER"):` 之前加：

```python
        if strip_numbers:
            audio.delall("TRCK"); audio.delall("TPOS")
```

`app/libops.py` 追加：

```python
def migrate_singles(library: str | None = None, target_library: str = "singles",
                    artist: str | None = None, dry_run: bool = False) -> dict:
    """扫描专辑库中只有一个音频文件的专辑目录，迁移到 singles 库 {目标根}/{艺人}/{曲名.ext}。

    迁移后重写 tag：清除序号类（TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL），
    保留 ALBUM/ARTIST/DATE/封面/歌词；lyrics/ 中同名 .lrc 移到目标旁；
    原专辑目录整目录删除，空艺人目录一并清理；目标已存在同名文件则跳过。
    """
    src_root = Path(resolve_library_root(library))
    dst_root = Path(resolve_library_root(target_library))
    migrated: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    if artist:
        artist_dirs = [src_root / _safe_name(t2s(artist))]
        if not artist_dirs[0].is_dir():
            raise LookupError(f"艺人目录不存在: {artist_dirs[0]}")
    else:
        artist_dirs = sorted(d for d in src_root.iterdir() if d.is_dir())

    for adir in artist_dirs:
        for album_dir in sorted(d for d in adir.iterdir() if d.is_dir()):
            audios = _audio_files(album_dir)
            if len(audios) != 1:
                continue
            src = audios[0]
            tags = _read_tags(src)
            title = tags.get("title") or _strip_nn(src.stem)
            dst = dst_root / adir.name / f"{_safe_name(t2s(title))}{src.suffix.lower()}"
            item = {"from": str(src), "to": str(dst)}
            if dst.exists():
                skipped.append({**item, "reason": "目标已存在"})
                continue
            if dry_run:
                migrated.append(item)
                continue
            lrc_candidates = [album_dir / "lyrics" / f"{src.stem}.lrc", src.with_suffix(".lrc")]
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.rename(src, dst)
                except OSError:  # 跨设备
                    shutil.copy2(src, dst)
                    src.unlink()
                lyric_text = None
                for lrc in lrc_candidates:
                    if lrc.is_file():
                        lyric_text = lrc.read_text(encoding="utf-8", errors="ignore").strip() or None
                        shutil.move(str(lrc), str(dst.with_suffix(".lrc")))
                        break
                if dst.suffix.lstrip(".").lower() in _TAGGABLE_EXTS:
                    from .archive import _break_link_if_needed, _write_tags
                    _break_link_if_needed(dst)  # 防硬链接回改下载目录源文件
                    _write_tags(dst, title, adir.name, tags.get("album") or t2s(album_dir.name),
                                tags.get("date") or "", strip_numbers=True, lyric_text=lyric_text)
                shutil.rmtree(album_dir, ignore_errors=True)
                _rmdir_if_empty(adir, src_root)
                migrated.append(item)
            except Exception as e:
                errors.append(f"{src}: {type(e).__name__}: {e}")
    status = "success" if not errors else ("failed" if not migrated and not skipped else "partial")
    return {"status": status, "dry_run": dry_run, "migrated": migrated,
            "skipped": skipped, "errors": errors}
```

`app/main.py` 追加端点：

```python
@app.post("/api/v1/library/migrate_singles", dependencies=[Depends(auth)])
def api_migrate_singles(req: MigrateSinglesRequest) -> dict:
    try:
        return libops.migrate_singles(library=req.library, target_library=req.target_library,
                                      artist=req.artist, dry_run=req.dry_run)
    except (ValueError, LookupError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`mcp_adapter.py` 追加：

```python
@mcp.tool()
def migrate_singles(library: str | None = None, target_library: str = "singles",
                    artist: str | None = None, dry_run: bool = False) -> dict:
    """扫描专辑库中只有一个音频文件的专辑目录（单曲专辑），迁移到 singles 库。

    迁移后清除曲目序号类 tag（保留专辑名/封面/歌词），同名 .lrc 一并移动，
    原专辑目录与空艺人目录自动清理；目标已存在同名文件则跳过。

    Args:
        library: 源库名（可选，见 list_libraries；留空用默认库）
        target_library: 目标库名（默认 singles）
        artist: 限定单个艺人（可选；留空扫描整个源库）
        dry_run: True 时只报告将迁移的项，不实际迁移（建议先跑一遍确认范围）
    Returns:
        migrated（from/to 列表）、skipped（含原因）、errors。
    """
    payload: dict[str, Any] = {"target_library": target_library, "dry_run": dry_run}
    if library:
        payload["library"] = library
    if artist:
        payload["artist"] = artist
    with _client() as c:
        r = c.post("/api/v1/library/migrate_singles", json=payload)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: 运行测试 + 导入自检**

Run: `.venv/bin/python -m pytest tests/ -v && .venv/bin/python -c "import app.main, mcp_adapter; print('import ok')"`
Expected: 全部通过 + import ok

---

### Task 5: 场景 2——replace_album_track + REST + MCP

**Files:**
- Modify: `app/libops.py`（追加 `replace_album_track`）
- Modify: `app/main.py`（追加端点）
- Modify: `mcp_adapter.py`（追加 `replace_album_track` 工具）
- Test: `tests/test_libops.py`（追加离线可测部分：定位/比较/kept 路径用 monkeypatch 替换 `match_track` 与 `download_songs`）

**Interfaces:**
- Consumes: Task 1 基础件；`album.match_track`；`download.download_songs`/`_find_downloaded_file`；`archive._write_tags`；`schemas.AlbumInfo`/`AlbumTrack`
- Produces: `replace_album_track(library: str | None, artist: str, album: str, track: Any, sources: list[str] | None = None, force: bool = False, max_size_mb: float | None = None) -> dict`（返回 `{status, action: replaced/kept/unmatched/failed, old, new, error}`）；REST `POST /api/v1/library/replace_track`；MCP `replace_album_track(artist, album, track, library=None, sources=None, force=False, max_size_mb=None)`

- [ ] **Step 1: 写失败测试（追加）**

```python
def _fake_track(ext: str, source: str = "MiguMusicClient"):
    from app.schemas import Track
    return Track(id=f"{source}:x1", source=source, title="简单爱", artists=["周杰伦"],
                 album="范特西", ext=ext, quality="lossless" if ext == "flac" else "128k",
                 raw={"identifier": "x1"})


def test_replace_track_kept_when_not_better(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    _touch(root / "周杰伦" / "范特西" / "02 - 简单爱.flac")  # 已是 flac（tier 3）
    monkeypatch.setattr("app.album.match_track",
                        lambda *a, **k: {"status": "matched", "track": _fake_track("flac"),
                                         "match": {"score": 0.9}})
    r = libops.replace_album_track("t", "周杰伦", "范特西", 2)
    assert r["action"] == "kept"
    assert r["old"]["tier"] == 3 and r["new"]["tier"] == 3


def test_replace_track_replaced(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    old = _touch(root / "周杰伦" / "范特西" / "02 - 简单爱.mp3")  # tier 1
    monkeypatch.setattr("app.album.match_track",
                        lambda *a, **k: {"status": "matched", "track": _fake_track("flac"),
                                         "match": {"score": 0.9}})

    def _fake_download(source, song_dicts, save_dir):
        (Path(save_dir) / "简单爱.wav").write_bytes(b"new")  # wav 跳过写 tag

    monkeypatch.setattr("app.download.download_songs", _fake_download)
    monkeypatch.setattr("app.download._find_downloaded_file", lambda *a: "简单爱.wav")
    monkeypatch.setattr(settings, "download_root", str(tmp_path / "dl"))
    r = libops.replace_album_track("t", "周杰伦", "范特西", 2)
    assert r["action"] == "replaced"
    assert not old.exists()
    assert (root / "周杰伦" / "范特西" / "02 - 简单爱.wav").exists()


def test_replace_track_unmatched(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    _touch(root / "周杰伦" / "范特西" / "02 - 简单爱.mp3")
    monkeypatch.setattr("app.album.match_track",
                        lambda *a, **k: {"status": "unmatched", "error": "无候选结果", "match": None})
    r = libops.replace_album_track("t", "周杰伦", "范特西", 2)
    assert r["action"] == "unmatched"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_libops.py -k replace_track -v`
Expected: FAIL

- [ ] **Step 3: 实现**

`app/libops.py` 追加：

```python
def replace_album_track(library: str | None, artist: str, album: str, track: Any,
                        sources: list[str] | None = None, force: bool = False,
                        max_size_mb: float | None = None) -> dict:
    """重新搜索专辑中指定曲目，用更高音质版本替换（同步）。

    新候选 quality_tier 高于现有文件（无损 3 / ≥320k 2 / 其他 1）或 force=True 时才替换；
    序号/专辑/艺人/日期沿用旧 tag，封面用专辑目录 cover.*，歌词用新下载 .lrc 并更新 lyrics/。
    """
    from . import download as dl
    from .album import match_track
    from .archive import _write_tags
    from .schemas import AlbumInfo, AlbumTrack

    root = Path(resolve_library_root(library))
    album_dir = root / _safe_name(t2s(artist)) / _safe_name(t2s(album))
    if not album_dir.is_dir():
        raise LookupError(f"专辑目录不存在: {album_dir}")
    hits = find_album_track_files(album_dir, [track])
    if not hits:
        raise LookupError(f"专辑内未找到匹配曲目: {track}")
    old = hits[0]
    old_tags = _read_tags(old)
    old_tier = file_quality_tier(old)
    old_info = {"file": str(old.relative_to(root)), "ext": old.suffix.lstrip("."), "tier": old_tier}
    title = old_tags.get("title") or _strip_nn(old.stem)

    def _num(v: Any, default: int) -> int:
        try:
            return int(str(v).split("/")[0])
        except (TypeError, ValueError):
            return default

    expected = AlbumTrack(disc=_num(old_tags.get("discnumber"), 1),
                          track=_num(old_tags.get("tracknumber"), 0),
                          title=title, artists=[t2s(artist)],
                          duration_s=old_tags.get("duration_s"))
    r = match_track(expected,
                    AlbumInfo(collection_id="", title=t2s(album), artists=[t2s(artist)]),
                    sources, max_size_mb=max_size_mb)
    if r["status"] != "matched":
        return {"status": "success", "action": "unmatched", "old": old_info, "new": None,
                "error": r.get("error")}
    chosen = r["track"]
    new_tier = quality_tier(chosen)
    new_info: dict[str, Any] = {"source": chosen.source, "title": chosen.title, "ext": chosen.ext,
                                "quality": chosen.quality, "tier": new_tier,
                                "score": r["match"]["score"]}
    if new_tier <= old_tier and not force:
        return {"status": "success", "action": "kept", "old": old_info, "new": new_info,
                "error": None}

    tmp = Path(settings.download_root) / f"replace_{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        before = {p.name for p in tmp.iterdir()}
        dl.download_songs(chosen.source, [dict(chosen.raw)], str(tmp))
        fname = dl._find_downloaded_file(str(tmp), "", str(chosen.raw.get("identifier", "")), before)
        if not fname:
            return {"status": "success", "action": "failed", "old": old_info, "new": new_info,
                    "error": "下载后未找到落盘文件"}
        new_src = tmp / fname
        new_ext = new_src.suffix.lstrip(".").lower()
        m = re.match(r"^(\d+\s*-\s*)", old.name)
        prefix = m.group(1) if m else ""
        new_file = old.with_name(f"{prefix}{_safe_name(t2s(title))}.{new_ext}")
        if new_file.exists():
            new_file.unlink()
        shutil.move(str(new_src), str(new_file))
        if old.exists() and old != new_file:
            old.unlink()
        # 歌词：新下载的 .lrc 嵌入 tag 并更新 lyrics/（旧 stem 不同名的 .lrc 清掉）
        lyric_text = None
        new_lrc = new_src.with_suffix(".lrc")
        lyrics_dir = album_dir / "lyrics"
        if new_lrc.is_file():
            lyric_text = new_lrc.read_text(encoding="utf-8", errors="ignore").strip() or None
            lyrics_dir.mkdir(exist_ok=True)
            shutil.move(str(new_lrc), str(lyrics_dir / f"{new_file.stem}.lrc"))
        old_lrc = lyrics_dir / f"{old.stem}.lrc"
        if old_lrc.is_file() and old_lrc.name != f"{new_file.stem}.lrc":
            old_lrc.unlink()
        if new_ext in _TAGGABLE_EXTS:
            numbers: dict[str, str] = {}
            tn = old_tags.get("tracknumber") or ""
            if tn:
                numbers["TRACKNUMBER"] = tn
                if "/" in tn:
                    numbers["TRACKTOTAL"] = tn.split("/")[1]
            dn = old_tags.get("discnumber") or ""
            if dn:
                numbers["DISCNUMBER"] = dn
                if "/" in dn:
                    numbers["DISCTOTAL"] = dn.split("/")[1]
            cover_bytes = None
            for c in (album_dir / "cover.jpg", album_dir / "cover.png",
                      old.parent / "cover.jpg", old.parent / "cover.png"):
                if c.is_file():
                    cover_bytes = c.read_bytes()
                    break
            _write_tags(new_file, title, t2s(artist), t2s(album), old_tags.get("date") or "",
                        numbers=numbers, cover_bytes=cover_bytes, lyric_text=lyric_text)
        new_info["file"] = str(new_file.relative_to(root))
        return {"status": "success", "action": "replaced", "old": old_info, "new": new_info,
                "error": None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

`app/main.py` 追加端点：

```python
@app.post("/api/v1/library/replace_track", dependencies=[Depends(auth)])
def api_replace_track(req: ReplaceTrackRequest) -> dict:
    try:
        return libops.replace_album_track(library=req.library, artist=req.artist, album=req.album,
                                          track=req.track, sources=req.sources, force=req.force,
                                          max_size_mb=req.max_size_mb)
    except (ValueError, LookupError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
```

`mcp_adapter.py` 追加：

```python
@mcp.tool()
def replace_album_track(artist: str, album: str, track: str, library: str | None = None,
                        sources: str | None = None, force: bool = False,
                        max_size_mb: float | None = None) -> dict:
    """重新搜索专辑中指定曲目，用规格更高、更好的版本替换（同步）。

    新候选音质高于现有文件（无损 > 320k > 其他）时才替换，否则返回 action=kept；
    force=True 强制替换。替换后序号/专辑/艺人/日期沿用旧 tag，封面沿用专辑 cover。

    Args:
        artist: 艺人名（库内一级目录）
        album: 专辑名（库内二级目录）
        track: 曲目序号（如 "3"）或曲名
        library: 库名（可选，见 list_libraries；留空用默认库）
        sources: 逗号分隔的源名（可选，留空用默认五源）
        force: 新候选音质不高于现有版本也强制替换
        max_size_mb: 单文件体积上限（MB，可选）
    Returns:
        action（replaced/kept/unmatched/failed）、old/new 规格信息、error。
    """
    payload: dict[str, Any] = {"artist": artist, "album": album, "track": track, "force": force}
    if library:
        payload["library"] = library
    if sources:
        payload["sources"] = [s.strip() for s in sources.split(",") if s.strip()]
    if max_size_mb:
        payload["max_size_mb"] = max_size_mb
    with _client() as c:
        r = c.post("/api/v1/library/replace_track", json=payload)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: 运行测试 + 导入自检**

Run: `.venv/bin/python -m pytest tests/ -v && .venv/bin/python -c "import app.main, mcp_adapter; print('import ok')"`
Expected: 全部通过 + import ok

---

### Task 6: 文档同步 + 本地 E2E 验证

**Files:**
- Modify: `docs/API.md`、`docs/MCP.md`、`ROADMAP.md`

- [ ] **Step 1: docs/API.md 更新**

- `POST /api/v1/albums/{collection_id}/download` 段落补充：配置了 `singles` 命名库时，逐曲匹配前会先在 singles 库查找同专辑曲目，命中则不重复下载（manifest 的 `match.source` 为 `"singles"` 且带 `reused_from` 路径），归档成功后该曲目会从 singles 库迁移（删除源文件并清理空艺人目录）。
- manifest.json 格式说明的 `match` 字段补充 `reused_from`。
- 接口列表新增三节：`POST /api/v1/library/replace_track`、`POST /api/v1/library/cleanup`、`POST /api/v1/library/migrate_singles`，字段与响应按 Task 3/4/5 的 schemas 与返回值如实描述；强调 dry_run 用法与"空目录一并清理"语义。

- [ ] **Step 2: docs/MCP.md 更新**

工具清单新增 `replace_album_track` / `cleanup_library` / `migrate_singles` 三行说明；`download_album` 说明补 singles 复用行为。

- [ ] **Step 3: ROADMAP.md 更新**

M4 区新增已完成条目 **2e. 媒体库生命周期管理**（2026-08-13）：四场景各一行摘要，指向新端点/工具。

- [ ] **Step 4: 本地 E2E 验证（不依赖 Docker）**

用临时库根起本地服务并跑通三个新端点：

```bash
cd /home/yangds/github/media-music-service
mkdir -p /tmp/mms-e2e/{library,singles,downloads,data}
cat > /tmp/mms-e2e/config.yaml <<'EOF'
server:
  host: "127.0.0.1"
  port: 8765
download_root: "/tmp/mms-e2e/downloads"
db_path: "/tmp/mms-e2e/data/music_service.db"
library_root: "/tmp/mms-e2e/library"
extra_library_roots:
  singles: "/tmp/mms-e2e/singles"
cleanup:
  after_archive: false
  periodic: false
EOF
# 造库内夹具：单曲专辑 + 两曲专辑
mkdir -p "/tmp/mms-e2e/library/周杰伦/范特西 - Single" "/tmp/mms-e2e/library/周杰伦/范特西"
echo x > "/tmp/mms-e2e/library/周杰伦/范特西 - Single/01 - 蜗牛.wav"
echo x > "/tmp/mms-e2e/library/周杰伦/范特西/01 - 爱在西元前.wav"
echo x > "/tmp/mms-e2e/library/周杰伦/范特西/02 - 简单爱.wav"
MUSIC_SERVICE_CONFIG=/tmp/mms-e2e/config.yaml .venv/bin/python -m uvicorn app.main:app --port 8765 &
sleep 3
curl -s http://127.0.0.1:8765/api/v1/health
curl -s -X POST http://127.0.0.1:8765/api/v1/library/migrate_singles -H 'Content-Type: application/json' -d '{"dry_run": true}'
curl -s -X POST http://127.0.0.1:8765/api/v1/library/migrate_singles -H 'Content-Type: application/json' -d '{}'
curl -s -X POST http://127.0.0.1:8765/api/v1/library/cleanup -H 'Content-Type: application/json' -d '{"artist": "周杰伦", "album": "范特西", "tracks": [1]}'
curl -s -X POST http://127.0.0.1:8765/api/v1/library/cleanup -H 'Content-Type: application/json' -d '{"artist": "周杰伦", "album": "范特西", "tracks": [2]}'
curl -s -X POST http://127.0.0.1:8765/api/v1/library/replace_track -H 'Content-Type: application/json' -d '{"artist": "周杰伦", "album": "不存在", "track": 1}'
kill %1
```

预期：
- health 返回 ok；
- migrate dry_run 报告 1 项、不实际迁移；正式跑后 singles 库出现 `周杰伦/蜗牛.wav`，原单曲专辑目录消失；
- cleanup 删 01 后专辑目录仍在，删 02 后专辑目录与艺人目录消失；
- replace_track 对不存在专辑返回 400。
- 场景 1 的在线复用链路（download_album → singles 命中 → archive 迁移）已由 Task 2 的归档测试覆盖入库端；下载端行为用 `_run_album` 代码审查 + 线上实测确认（需真实音乐源，留给部署后验证）。
