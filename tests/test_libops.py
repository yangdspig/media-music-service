"""libops 基础件单元测试：匹配、音质分档、曲目定位、安全边界。"""
import os
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


def test_run_album_reuse_skips_match_track(tmp_path, monkeypatch):
    """singles 命中时直接复用：不调用 match_track（不搜索不下载），产物写入 manifest。"""
    import json
    from app import album as album_svc
    from app.schemas import AlbumInfo, AlbumTrack, DownloadTask

    singles = tmp_path / "singles"
    save_dir = tmp_path / "dl"
    save_dir.mkdir()
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(singles)})
    monkeypatch.setattr(album_svc.dl, "save_task", lambda task: None)  # 避免落库

    def _forbidden(*args, **kwargs):
        raise AssertionError("singles 命中时不应调用 match_track")
    monkeypatch.setattr(album_svc, "match_track", _forbidden)

    src_single = _touch(singles / "周杰伦" / "蜗牛.wav")
    _touch(singles / "周杰伦" / "蜗牛.lrc")
    task = DownloadTask(task_id="t-reuse", total=1, save_dir=str(save_dir))
    album = AlbumInfo(collection_id="c", title="范特西", artists=["周杰伦"],
                      tracks=[AlbumTrack(disc=1, track=1, title="蜗牛", artists=["周杰伦"])])

    album_svc._run_album(task, album, sources=None)

    assert task.completed == 1
    assert (save_dir / "01 蜗牛.wav").exists()          # 硬链接落盘
    assert (save_dir / "01 蜗牛.lrc").exists()          # 同名歌词一并复制
    manifest = json.loads((save_dir / "manifest.json").read_text(encoding="utf-8"))
    tr = manifest["tracks"][0]
    assert tr["status"] == "ok"
    assert tr["match"]["source"] == "singles"
    assert tr["match"]["reused_from"] == str(src_single)


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


def test_cleanup_library_rejects_path_traversal(tmp_path, monkeypatch):
    """artist/album 为 '.'/'..' 时抛 ValueError，库外与库内任何东西都不删。"""
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    album_dir = _make_album(root)
    outside = _touch(tmp_path / "keep.txt")
    for bad_artist, bad_album in (("..", "专辑"), (".", "专辑"), ("艺人", "..")):
        with pytest.raises(ValueError):
            libops.cleanup_library("t", bad_artist, bad_album)
    assert outside.exists()          # 库外文件未被 rmtree 波及
    assert album_dir.exists()        # 库内目录原样保留
    assert (album_dir / "01 - a.wav").exists()


def test_cleanup_library_unlink_error_reports_partial(tmp_path, monkeypatch):
    """单个文件 unlink 失败：记 errors、status=partial，失败文件不进 deleted_files。"""
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    album_dir = _make_album(root)
    target = album_dir / "01 - a.wav"
    real_unlink = Path.unlink

    def _flaky(self, *args, **kwargs):
        if self == target:
            raise PermissionError("denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _flaky)
    r = libops.cleanup_library("t", "艺人", "专辑", tracks=[1])
    assert r["status"] == "partial"
    assert r["errors"]
    assert str(target) not in r["deleted_files"]
    assert target.exists()                                # 删除失败的文件仍在
    assert album_dir.exists()                             # 有音频残留，不做整目录清理
    assert str(album_dir / "lyrics" / "01 - a.lrc") in r["deleted_files"]  # 成功项仍计入


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
    monkeypatch.setattr("app.download.download_songs",
                        lambda *a: pytest.fail("kept 分支不应触发下载"))
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


def test_replace_track_rejects_symlink_escape(tmp_path, monkeypatch):
    """库内专辑目录是指向库外的符号链接时抛 ValueError，库外文件绝不动。"""
    import os
    root = tmp_path / "lib"
    monkeypatch.setattr(settings, "extra_library_roots", {"t": str(root)})
    outside = _touch(tmp_path / "elsewhere" / "范特西" / "02 - 简单爱.mp3")
    (root / "周杰伦").mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere" / "范特西", root / "周杰伦" / "范特西")

    def _forbidden(*a, **k):
        raise AssertionError("越界校验应在搜索/下载之前生效")
    monkeypatch.setattr("app.album.match_track", _forbidden)
    with pytest.raises(ValueError):
        libops.replace_album_track("t", "周杰伦", "范特西", 2)
    assert outside.exists()  # 库外文件原样保留


def test_find_album_track_files_disc_track_disambiguation(tmp_path):
    """多碟专辑 CD1/03 与 CD2/03 并存时，"2-03" 限定命中 CD2 目录下的文件。"""
    d = tmp_path / "艺人" / "专辑"
    _touch(d / "CD1" / "03 - 忍者.flac")
    _touch(d / "CD2" / "03 - 双截棍.flac")
    hits = libops.find_album_track_files(d, ["2-03"])
    assert [str(p.relative_to(d)) for p in hits] == ["CD2/03 - 双截棍.flac"]
    # int/"3" 形式行为不变：仍命中排序在前的 CD1 文件
    assert libops.find_album_track_files(d, [3])[0].name == "03 - 忍者.flac"


def test_migrate_singles_skips_target_inside_source(tmp_path, monkeypatch):
    """目标库根挂在源库内部时（如 singles/ 子目录），该子树不被误判为单曲专辑自我搬迁。"""
    src = tmp_path / "lib"
    dst = src / "singles"  # 目标库根在源库之内（生产环境 /library/singles 挂载为 /singles 的形态）
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    existing = _touch(dst / "古巨基" / "好想好想.flac")   # 已在 singles 库的文件
    _touch(src / "周杰伦" / "范特西 - Single" / "01 - 蜗牛.wav")  # 真正的单曲专辑
    r = libops.migrate_singles()
    assert r["errors"] == []
    assert existing.exists()  # singles 库自身内容原样不动
    assert all("古巨基" not in m["from"] for m in r["migrated"])
    assert (dst / "周杰伦" / "蜗牛.wav").exists()  # 正常迁移不受影响


def test_same_dir(tmp_path):
    d = tmp_path / "a"
    d.mkdir()
    assert libops._same_dir(d, d)
    assert libops._same_dir(d, tmp_path / "a" / ".." / "a")
    other = tmp_path / "b"
    other.mkdir()
    assert not libops._same_dir(d, other)
    assert not libops._same_dir(d, tmp_path / "nonexistent")


def test_migrate_singles_syncs_artist_image(tmp_path, monkeypatch):
    """艺人仍有其他专辑时：头像复制到 singles 艺人目录，源头像保留。"""
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "阿杜" / "artist.jpg")
    _touch(src / "阿杜" / "新家" / "01 - 新家.wav")
    _touch(src / "阿杜" / "天黑" / "01 - a.wav")
    _touch(src / "阿杜" / "天黑" / "02 - b.wav")
    r = libops.migrate_singles()
    assert r["status"] == "success"
    assert (dst / "阿杜" / "artist.jpg").exists()       # 头像已同步
    assert (src / "阿杜" / "artist.jpg").exists()       # 源头像保留（还有其他专辑）
    assert (src / "阿杜" / "天黑").exists()


def test_migrate_singles_moves_artist_image_when_no_albums_left(tmp_path, monkeypatch):
    """艺人在专辑库只剩单曲专辑时：头像搬到 singles，源艺人目录整目录清掉。"""
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "陈奕迅" / "artist.jpg")
    _touch(src / "陈奕迅" / "孤勇者" / "01 - 孤勇者.wav")
    r = libops.migrate_singles()
    assert r["status"] == "success"
    assert (dst / "陈奕迅" / "artist.jpg").exists()
    assert not (src / "陈奕迅").exists()                # 源艺人目录整体清掉


def test_migrate_singles_artist_image_dedup_when_target_has_one(tmp_path, monkeypatch):
    """目标已有头像且源只剩头像时：保留目标头像，源侧去重。"""
    src = tmp_path / "lib"
    dst = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(src))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(dst)})
    _touch(src / "陈奕迅" / "artist.jpg")
    _touch(src / "陈奕迅" / "孤勇者" / "01 - 孤勇者.wav")
    target_img = _touch(dst / "陈奕迅" / "artist.jpg")
    target_img.write_bytes(b"target-version")
    r = libops.migrate_singles()
    assert r["status"] == "success"
    assert target_img.read_bytes() == b"target-version"  # 目标头像不被覆盖
    assert not (src / "陈奕迅").exists()
