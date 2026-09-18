"""歌词回填单元测试：搜索层 mock，离线运行。"""
import struct
from pathlib import Path

import pytest

from app import backfill
from app.config import settings
from app.schemas import Track


def _fake_flac(p: Path) -> Path:
    """最小可解析 FLAC：fLaC + STREAMINFO 块（44.1kHz/2ch/16bit，时长 0）。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    x = (44100 << 44) | (1 << 41) | (15 << 36)
    streaminfo = struct.pack(">HH", 4096, 4096) + bytes(6) + x.to_bytes(8, "big") + bytes(16)
    p.write_bytes(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + streaminfo)
    return p


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake")
    return p


@pytest.fixture()
def library_root(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(settings, "library_root", str(root))
    monkeypatch.setattr(settings, "extra_library_roots", {})
    return root


def _hit_track(title="龙卷风", artists=None, lyric="[00:01.00]歌词行\n"):
    return Track(id="S:1", source="S", title=title, artists=artists or ["周杰伦"],
                 lyric=lyric, raw={"identifier": "1"})


def test_scan_detects_sidecar_and_embedded(tmp_path, library_root, monkeypatch):
    """检测口径：sidecar 与内嵌歌词（mp3 USLT / flac LYRICS）都算已有，不触发搜索。"""
    monkeypatch.setattr(backfill, "search",
                        lambda **kw: pytest.fail("已有歌词的曲目不应触发搜索"))
    _touch(library_root / "周杰伦" / "范特西" / "01 - 简单爱.wav")
    _touch(library_root / "周杰伦" / "范特西" / "01 - 简单爱.lrc")
    mp3 = _touch(library_root / "周杰伦" / "范特西" / "02 - 安静.mp3")
    from mutagen.id3 import ID3, USLT
    tags = ID3()
    tags.add(USLT(encoding=3, lang="eng", desc="", text="[00:01.00]歌词"))
    tags.save(mp3)
    flac = _fake_flac(library_root / "周杰伦" / "范特西" / "03 - 开不了口.flac")
    from mutagen.flac import FLAC
    audio = FLAC(flac)
    audio["LYRICS"] = "[00:01.00]歌词"
    audio.save()

    r = backfill.backfill_lyrics()
    by_path = {t["path"]: t for t in r["tracks"]}
    assert by_path["周杰伦/范特西/01 - 简单爱.wav"]["status"] == "already_has_lyrics"
    assert by_path["周杰伦/范特西/02 - 安静.mp3"]["status"] == "already_has_lyrics"
    assert by_path["周杰伦/范特西/03 - 开不了口.flac"]["status"] == "already_has_lyrics"
    assert r["summary"] == {"already_has_lyrics": 3}


def test_backfill_dry_run_writes_nothing(tmp_path, library_root, monkeypatch):
    """dry_run（默认 True）只报告匹配结果，不落任何文件。"""
    monkeypatch.setattr(backfill, "search", lambda **kw: ([_hit_track()], []))
    _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    r = backfill.backfill_lyrics()
    assert r["dry_run"] is True
    tr = r["tracks"][0]
    assert tr["status"] == "matched"
    assert tr["score"] >= 0.6
    assert tr["matched"]["source"] == "S"
    assert not (library_root / "周杰伦" / "范特西" / "04 - 龙卷风.lrc").exists()


def test_backfill_writes_lrc(tmp_path, library_root, monkeypatch):
    """dry_run=False：命中后写同名 .lrc（UTF-8），音频文件不动。"""
    monkeypatch.setattr(backfill, "search", lambda **kw: ([_hit_track()], []))
    audio = _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    before = audio.read_bytes()
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "written"
    lrc = library_root / "周杰伦" / "范特西" / "04 - 龙卷风.lrc"
    assert "[00:01.00]歌词行" in lrc.read_text(encoding="utf-8")
    assert audio.read_bytes() == before


def test_backfill_never_overwrites_existing_lrc(tmp_path, library_root, monkeypatch):
    """已有 sidecar 的曲目直接跳过，手工放置的 .lrc 内容不变。"""
    monkeypatch.setattr(backfill, "search",
                        lambda **kw: pytest.fail("已有 sidecar 不应触发搜索"))
    _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    lrc = library_root / "周杰伦" / "范特西" / "04 - 龙卷风.lrc"
    lrc.write_text("手工歌词", encoding="utf-8")
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "already_has_lyrics"
    assert lrc.read_text(encoding="utf-8") == "手工歌词"


def test_backfill_identity_from_filename_when_tags_missing(tmp_path, library_root, monkeypatch):
    """tag 缺失时（如 wav）回退文件名去序号前缀 + 艺人目录名作为搜索关键词。"""
    seen = {}

    def _fake_search(keyword, sources=None, limit=20):
        seen["keyword"] = keyword
        return ([_hit_track()], [])

    monkeypatch.setattr(backfill, "search", _fake_search)
    _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    r = backfill.backfill_lyrics()
    assert r["tracks"][0]["status"] == "matched"
    assert "龙卷风" in seen["keyword"] and "周杰伦" in seen["keyword"]


def test_backfill_below_threshold_unmatched(tmp_path, library_root, monkeypatch):
    """标题/艺人相似度过低或有候选但无歌词：记 unmatched，不写文件。"""
    far = _hit_track(title="毫不相干的另一首歌xyz", artists=["李四"])
    monkeypatch.setattr(backfill, "search", lambda **kw: ([far], []))
    _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "unmatched"
    assert not (library_root / "周杰伦" / "范特西" / "04 - 龙卷风.lrc").exists()

    no_lyric = _hit_track()
    no_lyric.lyric = None
    monkeypatch.setattr(backfill, "search", lambda **kw: ([no_lyric], []))
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "unmatched"


def test_backfill_same_title_unrelated_artist_rejected(tmp_path, library_root, monkeypatch):
    """回归：标题完全相同、艺人完全无关的候选必须判 unmatched（即使它有歌词）。

    实测案例：本地「Girl」（林志颖）曾匹配到 Alexander 23 的「Girl」——
    ts=1.0、asim=0 时总分恰好压线 0.6，无艺人下限时会把无关歌词写进库。
    """
    wrong = _hit_track(title="Girl", artists=["Alexander 23"])
    monkeypatch.setattr(backfill, "search", lambda **kw: ([wrong], []))
    _touch(library_root / "林志颖" / "戏梦" / "01 - Girl.wav")
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "unmatched"
    assert not (library_root / "林志颖" / "戏梦" / "01 - Girl.lrc").exists()
    # 艺人部分一致（如「林志颖, 张娜拉」vs 目录名「林志颖」）仍可通过
    partial = _hit_track(title="Girl", artists=["林志颖", "张娜拉"])
    monkeypatch.setattr(backfill, "search", lambda **kw: ([partial], []))
    r = backfill.backfill_lyrics(dry_run=False)
    assert r["tracks"][0]["status"] == "written"


def test_backfill_limit_and_has_more(tmp_path, library_root, monkeypatch):
    """limit 限制单次处理曲目数，超出部分 has_more=True 留给下批。"""
    monkeypatch.setattr(backfill, "search", lambda **kw: ([_hit_track()], []))
    for i in (1, 2, 3):
        _touch(library_root / "周杰伦" / "范特西" / f"0{i} - 龙卷风.wav")
    r = backfill.backfill_lyrics(limit=2)
    assert r["scanned"] == 2
    assert r["has_more"] is True
    r = backfill.backfill_lyrics(limit=10)
    assert r["scanned"] == 3
    assert r["has_more"] is False


def test_backfill_artist_and_album_filters(tmp_path, library_root, monkeypatch):
    """artist/album 过滤缩小扫描范围；艺人目录不存在抛 LookupError。"""
    monkeypatch.setattr(backfill, "search", lambda **kw: ([_hit_track()], []))
    _touch(library_root / "周杰伦" / "范特西" / "04 - 龙卷风.wav")
    _touch(library_root / "周杰伦" / "叶惠美" / "01 - 晴天.wav")
    r = backfill.backfill_lyrics(artist="周杰伦", album="叶惠美")
    assert [t["path"] for t in r["tracks"]] == ["周杰伦/叶惠美/01 - 晴天.wav"]
    with pytest.raises(LookupError):
        backfill.backfill_lyrics(artist="不存在的艺人")
