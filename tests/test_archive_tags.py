"""归档 tag 流派写入/保留、全格式歌词 sidecar 与歌词状态字段的单元测试（离线）。"""
import json
import struct
from pathlib import Path

import pytest

from app import archive
from app.archive import _write_tags
from app.config import settings


def _fake_flac(p: Path) -> Path:
    """最小可解析 FLAC：fLaC + STREAMINFO 块（44.1kHz/2ch/16bit，时长 0）。"""
    x = (44100 << 44) | (1 << 41) | (15 << 36)
    streaminfo = struct.pack(">HH", 4096, 4096) + bytes(6) + x.to_bytes(8, "big") + bytes(16)
    p.write_bytes(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + streaminfo)
    return p


def test_write_tags_flac_genre_written(tmp_path):
    f = _fake_flac(tmp_path / "01 - 歌.flac")
    _write_tags(f, "歌", "艺人", "专辑", genre="國語流行樂")
    from mutagen.flac import FLAC
    assert FLAC(f)["GENRE"] == ["国语流行"]  # 繁转简 + normalize_genre 归一化


def test_write_tags_flac_genre_preserved_when_not_given(tmp_path):
    """不传 genre 时白名单含 GENRE，已有流派不被清掉（老库重跑归档不丢流派）。"""
    f = _fake_flac(tmp_path / "01 - 歌.flac")
    from mutagen.flac import FLAC
    audio = FLAC(f)
    audio["GENRE"] = "摇滚"
    audio.save()
    _write_tags(f, "歌", "艺人", "专辑")
    assert FLAC(f)["GENRE"] == ["摇滚"]


def test_write_tags_mp3_genre_written(tmp_path):
    f = tmp_path / "01 - 歌.mp3"
    f.write_bytes(b"fake mp3 bytes")
    _write_tags(f, "歌", "艺人", "专辑", genre="流行")
    from mutagen.id3 import ID3
    assert str(ID3(f)["TCON"].text[0]) == "流行"


def test_write_tags_mp3_genre_preserved_when_not_given(tmp_path):
    """MP3 不做白名单清理，重跑不传 genre 时已有 TCON 自然保留。"""
    f = tmp_path / "01 - 歌.mp3"
    f.write_bytes(b"fake mp3 bytes")
    _write_tags(f, "歌", "艺人", "专辑", genre="流行")
    _write_tags(f, "歌", "艺人", "专辑")
    from mutagen.id3 import ID3
    assert str(ID3(f)["TCON"].text[0]) == "流行"


@pytest.fixture()
def library_root(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(settings, "library_root", str(root))
    monkeypatch.setattr(settings, "extra_library_roots", {})
    return root


def _make_manifest(tmp_path, filename: str, ext: str, with_lrc: bool, genre=None) -> str:
    src = tmp_path / "downloads" / "task"
    src.mkdir(parents=True)
    (src / filename).write_bytes(b"fake audio")
    if with_lrc:
        (src / Path(filename).with_suffix(".lrc")).write_text(
            "[00:01.00]歌词行", encoding="utf-8")
    manifest = {
        "album": {"collection_id": "1", "title": "范特西", "artists": ["周杰伦"],
                  "release_date": "2001-09-14", "genre": genre, "meta_source": "itunes"},
        "cover": None,
        "tracks": [{"disc": 1, "track": 1, "title": "简单爱", "artists": ["周杰伦"],
                    "status": "ok", "file": filename, "ext": ext}],
    }
    mp = src / "manifest.json"
    mp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return str(mp)


def test_archive_sidecar_copied_for_untaggable_ext(tmp_path, library_root):
    """不可写 tag 的格式（wav）：sidecar .lrc 仍随音频入库，歌词状态 ok。"""
    mp = _make_manifest(tmp_path, "01 简单爱.wav", "wav", with_lrc=True)
    res = archive.archive_album(manifest_path=mp)
    album_dir = library_root / "周杰伦" / "范特西"
    tr = res.tracks[0]
    assert tr.action == "tag_unsupported"
    assert tr.lyric == "ok"
    assert (album_dir / "01 - 简单爱.lrc").read_text(encoding="utf-8") == "[00:01.00]歌词行"


def test_archive_lyric_missing_status(tmp_path, library_root):
    """下载产物无 .lrc：歌词状态 missing。"""
    mp = _make_manifest(tmp_path, "01 简单爱.mp3", "mp3", with_lrc=False)
    res = archive.archive_album(manifest_path=mp)
    assert res.tracks[0].lyric == "missing"
    assert not (library_root / "周杰伦" / "范特西" / "01 - 简单爱.lrc").exists()


def test_archive_album_writes_genre_tag(tmp_path, library_root):
    """manifest 的 album.genre 写入库内 flac 的 GENRE tag。"""
    src_dir = tmp_path / "downloads" / "task"
    mp = _make_manifest(tmp_path, "01 简单爱.flac", "flac", with_lrc=True, genre="国语流行乐")
    # 假 bytes 过不了 mutagen FLAC，换成最小合法 FLAC
    _fake_flac(src_dir / "01 简单爱.flac")
    res = archive.archive_album(manifest_path=mp)
    assert res.status == "success"
    from mutagen.flac import FLAC
    audio = FLAC(library_root / "周杰伦" / "范特西" / "01 - 简单爱.flac")
    assert audio["GENRE"] == ["国语流行"]  # manifest genre 经 normalize_genre 归一化
    assert res.tracks[0].lyric == "ok"


def test_archive_album_genre_artist_fallback(tmp_path, library_root, monkeypatch):
    """manifest genre 为空（中文源接管场景）：按艺人查 iTunes 流派兜底，写入前归一化。"""
    monkeypatch.setattr("app.itunes.get_artist_genre", lambda artist: "Mandopop")
    src_dir = tmp_path / "downloads" / "task"
    mp = _make_manifest(tmp_path, "01 简单爱.flac", "flac", with_lrc=False, genre=None)
    _fake_flac(src_dir / "01 简单爱.flac")
    res = archive.archive_album(manifest_path=mp)
    assert res.status == "success"
    from mutagen.flac import FLAC
    audio = FLAC(library_root / "周杰伦" / "范特西" / "01 - 简单爱.flac")
    assert audio["GENRE"] == ["国语流行"]


def test_archive_album_genre_fallback_miss_silent(tmp_path, library_root, monkeypatch):
    """艺人兜底也查不到：静默跳过，不写 GENRE（归档主流程不受影响）。"""
    monkeypatch.setattr("app.itunes.get_artist_genre", lambda artist: None)
    src_dir = tmp_path / "downloads" / "task"
    mp = _make_manifest(tmp_path, "01 简单爱.flac", "flac", with_lrc=False, genre=None)
    _fake_flac(src_dir / "01 简单爱.flac")
    res = archive.archive_album(manifest_path=mp)
    assert res.status == "success"
    from mutagen.flac import FLAC
    assert "GENRE" not in FLAC(library_root / "周杰伦" / "范特西" / "01 - 简单爱.flac")


def test_archive_tracks_genre_artist_fallback(tmp_path, monkeypatch):
    """单曲归档端到端：无专辑 genre 来源，按艺人 iTunes 兜底写 TCON（归一化后）。"""
    from mutagen.id3 import ID3
    from app import download as dl
    from app.schemas import DownloadTask

    root = tmp_path / "singles"
    monkeypatch.setattr(settings, "library_root", str(tmp_path / "lib"))
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(root)})
    monkeypatch.setattr(settings.cleanup, "after_archive", False)
    monkeypatch.setattr("app.itunes.get_artist_genre", lambda artist: "Mandopop")
    monkeypatch.setattr(archive, "_itunes_cover_fallback", lambda *a, **kw: None)
    monkeypatch.setattr("app.storage.upsert_task", lambda d: None)  # register_task 不落库

    src_dir = tmp_path / "dl"
    src_dir.mkdir()
    (src_dir / "十七岁的雨季.mp3").write_bytes(b"fake mp3 bytes")
    task = DownloadTask(task_id="t-single", save_dir=str(src_dir))
    task.results = [{"title": "十七岁的雨季", "artists": ["林志颖"],
                     "file": "十七岁的雨季.mp3", "save_dir": str(src_dir),
                     "ext": "mp3", "album": "十七岁的雨季", "cover_url": None}]
    dl.register_task(task)

    res = archive.archive_tracks("t-single", library="singles")
    assert res.status == "success"
    tags = ID3(root / "林志颖" / "十七岁的雨季.mp3")
    assert str(tags["TCON"].text[0]) == "国语流行"
