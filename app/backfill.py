"""歌词回填：扫描库内无歌词的音频文件，搜索匹配后写同名 .lrc sidecar。

背景与约束：
- 目标播放器（飞牛音乐）认同目录同名 .lrc sidecar，但对已入库文件不再重读 tag，
  故回填只写 sidecar 新文件（watcher 能扫到），绝不修改音频文件、不覆盖已有 .lrc；
- 已有 sidecar 或内嵌歌词（flac LYRICS / mp3 USLT / m4a ©lyr；wav/ape 等只看 sidecar）
  的曲目跳过；曲名/艺人优先取文件 tag，缺失时回退文件名（去 'NN - ' 前缀）与艺人目录名；
- 匹配复用专辑消歧的相似度打分（标题 0.6 / 艺人 0.4，阈值 0.6，另加艺人相似度
  下限 0.5 防同名歌错配无关艺人），只在带歌词的候选中择优；
- 网络密集型批量操作：与其他库运维一样同步执行，limit 限制单次处理曲目数，分批调用；
- dry_run=True（默认，安全）只扫描与匹配并报告，不落任何文件。
"""
from __future__ import annotations

from pathlib import Path

from .album import ACCEPT_THRESHOLD, _artist_sim, _safe_name, _sim, t2s
from .libops import _audio_files, _read_tags, _strip_nn
from .libraries import resolve_library_root
from .schemas import Track
from .search import search

_MATCH_LIMIT = 10  # 每首曲目聚合搜索的候选上限（回填只需带歌词的正确候选，不需专辑匹配的宽池）
_ARTIST_FLOOR = 0.5  # 艺人相似度下限：防标题完全相同的同名歌错配到无关艺人


def _has_embedded_lyrics(path: Path) -> bool:
    """内嵌歌词探测：flac LYRICS / mp3 USLT / m4a ©lyr；wav/ape 等只看 sidecar（返回 False）。

    读取失败一律视为无内嵌歌词（不阻塞扫描）。
    """
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext == "flac":
            from mutagen.flac import FLAC
            return bool("".join(FLAC(path).get("LYRICS") or []).strip())
        if ext == "mp3":
            from mutagen.id3 import ID3
            return any(str(f.text).strip() for f in ID3(path).getall("USLT"))
        if ext == "m4a":
            from mutagen.mp4 import MP4
            return bool("".join(MP4(path).get("©lyr") or []).strip())
    except Exception:
        pass
    return False


def _track_identity(path: Path, artist_dir_name: str) -> tuple[str, str]:
    """曲名/艺人：优先文件 tag，缺失时回退文件名（去 'NN - ' 前缀）与艺人目录名。"""
    tags = _read_tags(path)
    title = tags.get("title") or _strip_nn(path.stem)
    artist = tags.get("artist") or tags.get("albumartist") or artist_dir_name
    return title, artist


def _match_lyric(title: str, artist: str, sources: list[str] | None = None) -> dict | None:
    """按 曲名+艺人 聚合搜索，在带歌词的候选中按相似度择优（标题/总分阈值 0.6，
    艺人下限 0.5）；未命中返回 None。"""
    keyword = f"{t2s(title)} {t2s(artist)}".strip()
    if not keyword:
        return None
    try:
        candidates, _failed = search(keyword=keyword, sources=sources, limit=_MATCH_LIMIT)
    except Exception:
        return None
    best: Track | None = None
    best_score = 0.0
    for c in candidates:
        if not (c.lyric or "").strip():
            continue
        ts = _sim(title, c.title)
        asim = _artist_sim([artist] if artist else [], c.artists)
        score = 0.6 * ts + 0.4 * asim
        # 艺人相似度下限 0.5：标题完全相同（ts=1.0）而艺人完全无关（asim=0）时总分恰好
        # 压线 0.6，会把无关艺人的同名歌歌词写进库（实测「Girl」林志颖 → Alexander 23）；
        # 艺人部分一致（_sim 含子串规则给 0.9）或任一侧缺艺人信息（中性 0.5）仍可通过
        if (ts >= ACCEPT_THRESHOLD and asim >= _ARTIST_FLOOR
                and score >= ACCEPT_THRESHOLD and score > best_score):
            best, best_score = c, score
    if best is None:
        return None
    return {"track": best, "score": round(best_score, 3)}


def _iter_audio(artist_dirs: list[Path], album_filter: str | None):
    """按艺人目录遍历音频文件；album_filter 非空时只遍历同名专辑子目录。"""
    for adir in artist_dirs:
        if album_filter:
            album_dir = adir / album_filter
            files = _audio_files(album_dir) if album_dir.is_dir() else []
        else:
            files = _audio_files(adir)
        for f in files:
            yield adir, f


def backfill_lyrics(library: str | None = None, artist: str | None = None,
                    album: str | None = None, sources: list[str] | None = None,
                    limit: int = 50, dry_run: bool = True) -> dict:
    """扫描库内无歌词的音频文件并回填 sidecar 歌词（同步，幂等）。

    逐曲报告 status：already_has_lyrics（已有 sidecar/内嵌歌词，跳过）/
    matched（dry_run 下命中可回填）/ unmatched（未命中合格带歌词候选）/
    written（已写 .lrc）/ error（写文件失败）。limit 限制单次扫描处理的曲目数，
    超出部分置 has_more=True，调大 limit 或按 artist/album 过滤分批处理。
    """
    if limit < 1:
        raise ValueError("limit 必须 >= 1")
    root = Path(resolve_library_root(library))
    if artist:
        artist_dirs = [root / _safe_name(t2s(artist))]
        if not artist_dirs[0].is_dir():
            raise LookupError(f"艺人目录不存在: {artist_dirs[0]}")
    else:
        artist_dirs = sorted(d for d in root.iterdir() if d.is_dir())
    album_filter = _safe_name(t2s(album)) if album else None

    tracks: list[dict] = []
    has_more = False
    for adir, f in _iter_audio(artist_dirs, album_filter):
        if len(tracks) >= limit:
            has_more = True
            break
        item: dict = {"path": str(f.relative_to(root))}
        if f.with_suffix(".lrc").is_file() or _has_embedded_lyrics(f):
            item["status"] = "already_has_lyrics"
            tracks.append(item)
            continue
        title, artist_name = _track_identity(f, adir.name)
        item.update(title=title, artist=artist_name)
        hit = _match_lyric(title, artist_name, sources)
        if hit is None:
            item["status"] = "unmatched"
        else:
            best: Track = hit["track"]
            item.update(status="matched", score=hit["score"],
                        matched={"source": best.source, "title": best.title,
                                 "artists": best.artists})
            if not dry_run:
                try:
                    target = f.with_suffix(".lrc")
                    if not target.exists():  # 已有 sidecar 永不覆盖
                        target.write_text(best.lyric.strip() + "\n", encoding="utf-8")
                    item["status"] = "written"
                except Exception as e:
                    item.update(status="error", error=str(e))
        tracks.append(item)

    summary: dict[str, int] = {}
    for t in tracks:
        summary[t["status"]] = summary.get(t["status"], 0) + 1
    status = "partial" if summary.get("error") else "success"
    return {"status": status, "dry_run": dry_run, "library_dir": str(root),
            "scanned": len(tracks), "has_more": has_more, "summary": summary,
            "tracks": tracks}
