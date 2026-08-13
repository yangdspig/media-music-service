"""媒体库运维：singles 复用查找、指定曲目替换、库内清理、单曲专辑迁移。

安全红线（与 cleanup.py 同口径）：所有删除/移动只作用于白名单库根之内
（resolve_library_root 解析 + is_relative_to 校验），不碰库外任何路径。
"""
from __future__ import annotations

import os
import re
import shutil
import time
import uuid
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


def _path_within(p: Path, resolved_root: Path) -> bool:
    """路径解析后等于或位于已解析的根目录之内（含等于根本身）。"""
    try:
        return p.resolve().is_relative_to(resolved_root)
    except Exception:
        return False


def _same_dir(a: Path, b: Path) -> bool:
    """按 (st_dev, st_ino) 判断两路径是否同一目录。

    覆盖挂载别名场景：同一宿主目录经两个 bind mount 挂进容器（如 /library/singles
    与 /singles 指向同一目录），路径层面无法识别，只能靠 inode 比对。
    """
    try:
        sa, sb = a.stat(), b.stat()
        return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
    except Exception:
        return False


_ARTIST_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _is_artist_image(p: Path) -> bool:
    """是否 Navidrome 约定的艺人头像文件（artist.{jpg,jpeg,png,webp}）。"""
    return p.is_file() and p.stem.lower() == "artist" and p.suffix.lower() in _ARTIST_IMG_EXTS


def _sync_artist_image(src_artist_dir: Path, dst_artist_dir: Path) -> str | None:
    """迁移单曲时同步艺人头像：目标艺人目录无 artist.* 且源目录有，则复制一份（返回文件名）。"""
    try:
        if any(_is_artist_image(p) for p in dst_artist_dir.iterdir()):
            return None  # 目标已有头像，不动
    except Exception:
        pass
    try:
        for p in src_artist_dir.iterdir():
            if _is_artist_image(p):
                shutil.copy2(p, dst_artist_dir / p.name)
                return p.name
    except Exception:
        pass
    return None


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

    tracks 元素为 int/数字字符串（序号）、"D-NN"（disc-track，限定该 disc 对应
    CD{D}/ 子目录，无该子目录时退回主目录）或曲名（_sim >= 0.7 取最高分）；
    未命中的元素静默跳过。
    """
    files = _audio_files(album_dir)
    out: list[Path] = []
    for t in tracks:
        s = str(t).strip()
        hit: Path | None = None
        disc_track = re.fullmatch(r"(\d+)-(\d+)", s)
        if disc_track:
            disc, num = int(disc_track.group(1)), int(disc_track.group(2))
            cd_dir = album_dir / f"CD{disc}"
            pool = ([f for f in files if f.parent == cd_dir] if cd_dir.is_dir()
                    else [f for f in files if f.parent == album_dir])
            hit = next((f for f in pool if re.match(rf"^{num:02d}\s*-\s*", f.name)), None)
        elif s.isdigit():
            hit = next((f for f in files if re.match(rf"^{int(s):02d}\s*-\s*", f.name)), None)
        else:
            scored = sorted(((_sim(s, _strip_nn(f.stem)), f) for f in files),
                            key=lambda x: x[0], reverse=True)
            if scored and scored[0][0] >= 0.7:
                hit = scored[0][1]
        if hit and hit not in out:
            out.append(hit)
    return out


def cleanup_library(library: str | None, artist: str, album: str | None = None,
                    tracks: list[Any] | None = None, dry_run: bool = False) -> dict:
    """清理媒体库中的专辑/曲目文件，空目录一并清理（不留空目录）。

    粒度：tracks 指定曲目 > album 整专辑 > artist 整艺人。
    删除曲目后自底向上：空 CDx/ → 无音频残留的专辑目录（连同 cover/album_info/lyrics）→ 空艺人目录。
    dry_run=True 只报告不删除。
    """
    root = Path(resolve_library_root(library))
    artist_dir = root / _safe_name(t2s(artist))
    if not _under_root(artist_dir, root):
        raise ValueError(f"非法目录名（越界）: {artist}")
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
    if not _under_root(album_dir, root):
        raise ValueError(f"非法目录名（越界）: {album}")
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
    errors: list[str] = result["errors"]
    deleted_targets: list[Path] = []
    if dry_run:
        # 只报告将删的项，不动文件系统
        for f in targets:
            deleted_files.append(str(f))
            deleted_targets.append(f)
            for lrc in (album_dir / "lyrics" / f"{f.stem}.lrc", f.with_suffix(".lrc")):
                if lrc.is_file() and str(lrc) not in deleted_files:
                    deleted_files.append(str(lrc))
    else:
        for f in targets:
            try:
                f.unlink()
                deleted_files.append(str(f))
                deleted_targets.append(f)
            except Exception as e:
                errors.append(f"{f}: {e}")
            for lrc in (album_dir / "lyrics" / f"{f.stem}.lrc", f.with_suffix(".lrc")):
                try:
                    if lrc.is_file():
                        lrc.unlink()
                        if str(lrc) not in deleted_files:
                            deleted_files.append(str(lrc))
                except Exception as e:
                    errors.append(f"{lrc}: {e}")
        for cd in sorted(album_dir.iterdir()):
            if cd.is_dir() and re.fullmatch(r"CD\d+", cd.name, re.IGNORECASE):
                _rmdir_if_empty(cd, root)
    if errors:
        result["status"] = "partial"
    if not [f for f in _audio_files(album_dir) if f not in deleted_targets]:
        # 专辑已无音频残留：整目录删（含 cover/album_info/lyrics），再清空艺人目录
        if not dry_run:
            shutil.rmtree(album_dir, ignore_errors=True)
            if _rmdir_if_empty(artist_dir, root):
                removed_dirs.append(str(artist_dir))
        removed_dirs.insert(0, str(album_dir))
    return result


def migrate_singles(library: str | None = None, target_library: str = "singles",
                    artist: str | None = None, dry_run: bool = False) -> dict:
    """扫描专辑库中只有一个音频文件的专辑目录，迁移到 singles 库 {目标根}/{艺人}/{曲名.ext}。

    迁移后重写 tag：清除序号类（TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL），
    保留 ALBUM/ARTIST/DATE/封面/歌词；lyrics/ 中同名 .lrc 移到目标旁；
    原专辑目录整目录删除，空艺人目录一并清理；目标已存在同名文件则跳过。
    """
    src_root = Path(resolve_library_root(library))
    dst_root = Path(resolve_library_root(target_library))
    dst_resolved = dst_root.resolve()
    migrated: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    if artist:
        artist_dirs = [src_root / _safe_name(t2s(artist))]
        if not artist_dirs[0].is_dir():
            raise LookupError(f"艺人目录不存在: {artist_dirs[0]}")
    else:
        artist_dirs = sorted(d for d in src_root.iterdir() if d.is_dir())
    # 目标库根位于源库之内时（如 singles 挂在专辑库下的 singles/ 子目录，或同一目录
    # 经两个挂载点暴露为 /library/singles 与 /singles），跳过该子树：
    # 否则 singles 自己的艺人目录会被误判为"单曲专辑"而自我搬迁
    artist_dirs = [d for d in artist_dirs
                   if not (_path_within(d, dst_resolved) or _same_dir(d, dst_root))]

    for adir in artist_dirs:
        for album_dir in sorted(d for d in adir.iterdir() if d.is_dir()):
            if _path_within(album_dir, dst_resolved) or _same_dir(album_dir, dst_root):
                continue
            audios = _audio_files(album_dir)
            if len(audios) != 1:
                continue
            src = audios[0]
            tags = _read_tags(src)
            title = tags.get("title") or _strip_nn(src.stem)
            dst = dst_root / adir.name / f"{_safe_name(t2s(title))}{src.suffix.lower()}"
            item = {"from": str(src), "to": str(dst)}
            # 越界防护：目标/源路径必须严格位于各自库根内，不通过记 errors 并跳过
            if not _under_root(dst, dst_root):
                errors.append(f"{dst}: 目标路径越界（不在目标库根内）")
                continue
            if not (_under_root(album_dir, src_root) and _under_root(src, src_root)):
                errors.append(f"{album_dir}: 源路径越界（不在源库根内）")
                continue
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
                # 艺人头像同步：源艺人目录已无专辑、只剩 artist.* 时头像直接搬到目标
                # （目标已有则源侧去重）；仍有其他专辑时复制一份（目标已有不动）
                remaining = list(adir.iterdir()) if adir.is_dir() else []
                if remaining and all(_is_artist_image(p) for p in remaining):
                    for p in remaining:
                        try:
                            if (dst.parent / p.name).exists():
                                p.unlink()  # 目标已有头像，源侧去重
                            else:
                                shutil.move(str(p), str(dst.parent / p.name))
                        except Exception:
                            pass
                else:
                    _sync_artist_image(adir, dst.parent)
                _rmdir_if_empty(adir, src_root)
                migrated.append(item)
            except Exception as e:
                errors.append(f"{src}: {type(e).__name__}: {e}")
    status = "success" if not errors else ("failed" if not migrated and not skipped else "partial")
    return {"status": status, "dry_run": dry_run, "migrated": migrated,
            "skipped": skipped, "errors": errors}


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
    if not _under_root(album_dir, root):
        raise ValueError(f"非法专辑目录（越界）: {album_dir}")
    hits = find_album_track_files(album_dir, [track])
    if not hits:
        raise LookupError(f"专辑内未找到匹配曲目: {track}")
    old = hits[0]
    if not _under_root(old, root):
        raise ValueError(f"非法曲目文件（越界）: {old}")
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

    tmp = Path(settings.download_root) / f"replace_{int(time.time())}_{uuid.uuid4().hex[:6]}"
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
        # 旧文件旁同名 .lrc 一并清理（old 可能已 unlink，with_suffix 只是路径运算）
        side_lrc = old.with_suffix(".lrc")
        if side_lrc.is_file():
            side_lrc.unlink()
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
