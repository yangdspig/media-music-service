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
