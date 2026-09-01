"""合集（Various Artists）专辑归档单元测试：假 .mp3 走真实 ID3 写读。"""
import json

import pytest

from app import archive, libops
from app import download as dl
from app.archive import _VA_ARTIST, _is_compilation, _write_tags
from app.config import settings


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


# 最小可解析 MP3：3 个 MPEG-1 Layer III 帧（mutagen.File 需要同步到后续帧才认）
_FAKE_MP3 = (b"\xff\xfb\x90\x00" + b"\x00" * 413) * 3


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


def test_replace_track_preserves_va_tags(tmp_path, monkeypatch):
    """合集曲目替换：新文件沿用旧文件的逐曲 ARTIST、ALBUMARTIST=群星 与 COMPILATION。"""
    from app.schemas import Track
    root = tmp_path / "library"
    album_dir = root / "群星" / "仙剑奇侠传 电视原声带"
    album_dir.mkdir(parents=True)
    old = album_dir / "03 - 六月的雨.mp3"
    old.write_bytes(_FAKE_MP3)
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
                                                    "artist_img_url": None,
                                                    "candidates": 1, "oversized_filtered": 0,
                                                    "oversized_relaxed": False},
                                          "track": chosen})

    def _fake_download(source, songs, save_dir):
        from pathlib import Path as P
        (P(save_dir) / "new.mp3").write_bytes(_FAKE_MP3)
    monkeypatch.setattr(dl, "download_songs", _fake_download)
    monkeypatch.setattr(dl, "_find_downloaded_file", lambda *a, **kw: "new.mp3")

    res = libops.replace_album_track(None, "群星", "仙剑奇侠传 电视原声带", 3, force=True)
    assert res["action"] == "replaced"
    tags = _read_id3(album_dir / "03 - 六月的雨.mp3")
    assert str(tags["TPE1"].text[0]) == "胡歌"
    assert str(tags["TPE2"].text[0]) == "群星"
    assert str(tags["TCMP"].text[0]) == "1"
    assert str(tags["TRCK"].text[0]) == "3/13"
