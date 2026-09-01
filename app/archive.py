"""专辑归档入库：以 manifest.json 为契约，把专辑下载产物整理进媒体库。

设计要点（对应 ROADMAP M4-1 第二期）：
- 目标结构对齐 music-album-archiver skill 的库约定（Navidrome 兼容）：
  {library_root}/{艺人}/{专辑}/，曲目 `NN - 曲名.ext`；多 Disc 用 CD1/CD2 子目录
  并写 DISCNUMBER/DISCTOTAL tag，每个分碟目录放一份 cover.jpg；
- 优先硬链接（不占双份空间），CIFS/跨设备失败时回退复制；
- **改 tag 前必须先断链**（copy + os.replace）：硬链接共享 inode，
  原地写 tag 会把下载目录的源文件一起改掉（skill 实践教训）；
- 归档为同步操作（秒级），幂等：目标已存在且未指定 overwrite 时跳过；
- 输入只看 manifest.json，不解析 musicdl 私有 download_results.pkl。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

from . import download as dl
from .album import _safe_name, infer_display_names, t2s
from .config import settings
from .libraries import resolve_library_root
from .schemas import ArchiveResult, ArchiveTrackResult

_TAGGABLE_EXTS = {"flac", "mp3"}

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


def _resolve_names(album: dict, ok_entries: list[dict],
                   album_title: str | None = None, artist: str | None = None) -> tuple[str, str]:
    """显示名解析链：显式参数 > manifest display_* 字段 > 归档时自动推断 > iTunes 原名（转简体）。

    自动推断见 album.infer_display_names：iTunes 罗马音专辑名（如 "Kou Shi Xin Fei"）
    会被国内源候选的多数表决中文名替换；原名已含中文则保持不变。
    """
    inferred = infer_display_names(album, ok_entries)
    title = (album_title or album.get("display_title") or inferred.get("display_title")
             or t2s(album.get("title") or "未知专辑"))
    art = (artist or album.get("display_artist") or inferred.get("display_artist")
           or t2s((album.get("artists") or ["未知艺人"])[0]))
    return title, art


def _load_manifest(task_id: str | None, manifest_path: str | None) -> tuple[dict, Path]:
    """解析并加载 manifest：task_id 优先（查内存任务），否则用显式路径。"""
    path: Optional[str] = manifest_path
    if task_id:
        task = dl.get(task_id)
        if not task or not task.manifest_path:
            raise LookupError(f"任务 {task_id} 不在内存中或不是专辑任务（服务重启后请改用 manifest_path）")
        path = task.manifest_path
    if not path:
        raise ValueError("task_id 与 manifest_path 必填其一")
    p = Path(path)
    if not p.exists():
        raise LookupError(f"manifest 不存在: {path}")
    return json.loads(p.read_text(encoding="utf-8")), p.parent


def _break_link_if_needed(path: Path) -> None:
    """硬链接文件改 tag 前断链：复制副本再原子替换，下载源文件保持不动。"""
    if os.stat(path).st_nlink > 1:
        tmp = path.with_name(path.name + ".archtmp")
        shutil.copy2(path, tmp)
        os.replace(tmp, path)


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

    if ext == "flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(path)
        # 白名单重写：清掉平台水印等杂项后统一写入
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
        if album_title:
            audio["ALBUM"] = album_title
        audio["TITLE"] = title
        if date:
            audio["DATE"] = date
        if strip_numbers:
            for k in ("TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL"):
                if k in audio:
                    del audio[k]
        for k, v in numbers.items():
            audio[k] = v
        audio["COMMENT"] = comment
        if lyric_text:
            audio["LYRICS"] = lyric_text
        if cover_bytes:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = _cover_mime(cover_bytes)
            pic.desc = "cover"
            pic.data = cover_bytes
            audio.add_picture(pic)
        audio.save()
    elif ext == "mp3":
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
        if album_title:
            audio.delall("TALB"); audio.add(TALB(encoding=3, text=album_title))
        audio.delall("TIT2"); audio.add(TIT2(encoding=3, text=title))
        if date:
            audio.delall("TDRC"); audio.add(TDRC(encoding=3, text=date))
        if strip_numbers:
            audio.delall("TRCK"); audio.delall("TPOS")
        if numbers.get("TRACKNUMBER"):
            audio.delall("TRCK"); audio.add(TRCK(encoding=3, text=numbers["TRACKNUMBER"]))
        if numbers.get("DISCNUMBER"):
            audio.delall("TPOS"); audio.add(TPOS(encoding=3, text=numbers["DISCNUMBER"]))
        audio.delall("COMM"); audio.add(COMM(encoding=3, lang="eng", desc="", text=comment))
        if lyric_text:
            audio.delall("USLT"); audio.add(USLT(encoding=3, lang="eng", desc="", text=lyric_text))
        if cover_bytes:
            audio.delall("APIC")
            mime = _cover_mime(cover_bytes)
            audio.add(APIC(encoding=3, mime=mime, type=3, desc="cover", data=cover_bytes))
        audio.save(path)
    else:
        raise ValueError(f"不支持写 tag 的格式: {ext}")


def _target_relpath(entry: dict, multi_disc: bool) -> str:
    ext = (entry.get("ext") or "flac").lstrip(".")
    name = f"{entry['track']:02d} - {_safe_name(t2s(entry.get('title')))}.{ext}"
    return f"CD{entry['disc']}/{name}" if multi_disc else name


def _write_album_info(album_dir: Path, album: dict, entries: list[dict],
                      display_title: str, display_artist: str) -> None:
    """生成 album_info.txt：简介取自 manifest.album.description（网易云/QQ 补充），无则省略简介段。

    「iTunes 原名」标注与 storefront 仅在元数据来自 iTunes 系（meta_source 以 itunes 开头）时输出。
    """
    meta_source = album.get("meta_source") or "itunes"
    itunes_based = meta_source.startswith("itunes")
    orig_title = album.get("title") or ""
    orig_artists = " / ".join(album.get("artists") or [])
    source_line = f"元数据来源：{meta_source} (collection {album.get('collection_id')}"
    if itunes_based and album.get("storefront"):
        source_line += f", storefront {album.get('storefront')}"
    source_line += ")"
    lines = [
        f"专辑：{display_title}" + (f"（iTunes 原名：{orig_title}）" if itunes_based and orig_title != display_title else ""),
        f"艺人：{display_artist}" + (f"（iTunes 原名：{orig_artists}）" if itunes_based and orig_artists and orig_artists != display_artist else ""),
        f"发行日期：{(album.get('release_date') or '')[:10]}",
        f"流派：{album.get('genre') or ''}",
        source_line,
        "",
        "曲目表：",
    ]
    for e in entries:
        dur = e.get("duration_s")
        dur_s = f" ({int(dur // 60)}:{int(dur % 60):02d})" if dur else ""
        prefix = f"CD{e['disc']} " if len({x['disc'] for x in entries}) > 1 else ""
        lines.append(f"{prefix}{e['track']:02d}. {t2s(e.get('title'))}{dur_s}")
    description = (album.get("description") or "").strip()
    if description:
        lines += ["", "简介：", description]
    (album_dir / "album_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_album(task_id: str | None = None, manifest_path: str | None = None,
                  overwrite: bool = False, album_title: str | None = None,
                  artist: str | None = None, library: str | None = None) -> ArchiveResult:
    """按 manifest 把专辑下载产物归档进媒体库（同步，幂等）。

    目录名与 tag 用的专辑名/艺人名按解析链确定：
    显式 album_title/artist 参数 > manifest display_* > 自动推断 > iTunes 原名。
    library 为命名库根（见 libraries 模块），留空用默认库。
    """
    root = Path(resolve_library_root(library))
    manifest, src_dir = _load_manifest(task_id, manifest_path)
    album = manifest.get("album") or {}
    entries = manifest.get("tracks") or []
    ok_entries = [e for e in entries if e.get("status") == "ok" and e.get("file")]
    disp_title, disp_artist = _resolve_names(album, ok_entries, album_title, artist)
    album_dir = root / _safe_name(disp_artist) / _safe_name(disp_title)
    multi_disc = len({e["disc"] for e in entries}) > 1
    disc_total = max((e["disc"] for e in entries), default=1)
    cover_bytes: bytes | None = None
    if manifest.get("cover") and (src_dir / manifest["cover"]).exists():
        cover_bytes = (src_dir / manifest["cover"]).read_bytes()

    results: list[ArchiveTrackResult] = []
    for entry in ok_entries:
        rel = _target_relpath(entry, multi_disc)
        target = album_dir / rel
        res = ArchiveTrackResult(disc=entry["disc"], track=entry["track"],
                                 title=t2s(entry.get("title") or ""), target=rel, action="")
        try:
            if target.exists() and not overwrite:
                res.action = "skipped"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.unlink()
                src = src_dir / entry["file"]
                try:
                    os.link(src, target)
                    res.action = "linked"
                except OSError:
                    shutil.copy2(src, target)
                    res.action = "copied"
                ext = target.suffix.lstrip(".").lower()
                if ext in _TAGGABLE_EXTS:
                    _break_link_if_needed(target)
                    # sidecar 歌词（与音频同 stem 的 .lrc）
                    lrc_src = src.with_suffix(".lrc")
                    lyric_text = None
                    if lrc_src.exists():
                        lyric_text = lrc_src.read_text(encoding="utf-8", errors="ignore").strip() or None
                        lrc_dir = album_dir / "lyrics"
                        lrc_dir.mkdir(exist_ok=True)
                        shutil.copy2(lrc_src, lrc_dir / f"{target.stem}.lrc")
                    disc_track_total = sum(1 for e in ok_entries if e["disc"] == entry["disc"])
                    numbers = {"TRACKNUMBER": f"{entry['track']}/{disc_track_total}",
                               "TRACKTOTAL": str(disc_track_total)}
                    if disc_total > 1:
                        numbers["DISCNUMBER"] = f"{entry['disc']}/{disc_total}"
                        numbers["DISCTOTAL"] = str(disc_total)
                    _write_tags(target, entry.get("title") or "", disp_artist, disp_title,
                                (album.get("release_date") or "")[:10],
                                numbers=numbers, cover_bytes=cover_bytes, lyric_text=lyric_text)
                else:
                    res.action = "tag_unsupported"
        except Exception as e:  # 单曲失败不中断整体
            res.action = "failed"
            res.error = str(e)
        results.append(res)

    # 专辑级产物：cover.jpg（含分碟副本）、album_info.txt
    album_dir.mkdir(parents=True, exist_ok=True)
    if cover_bytes:
        suffix = ".png" if cover_bytes[:4] == b"\x89PNG" else ".jpg"
        (album_dir / f"cover{suffix}").write_bytes(cover_bytes)
        if multi_disc:
            for d in {e["disc"] for e in ok_entries}:
                cd_dir = album_dir / f"CD{d}"
                if cd_dir.exists():
                    (cd_dir / f"cover{suffix}").write_bytes(cover_bytes)
    _write_album_info(album_dir, album, entries, disp_title, disp_artist)
    # 艺人头像（幂等）：取首个带头像的成功条目写入艺人目录，已有 artist.* 则跳过
    img_url = next(((e.get("match") or {}).get("artist_img_url") for e in ok_entries
                    if (e.get("match") or {}).get("artist_img_url")), None)
    _save_artist_image(album_dir.parent, img_url)

    status, summary, errors = _summarize(results)
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
    # 归档后自动清理下载产物：全曲 ok 且入库无失败才算完全成功（整目录清，含 manifest）；
    # 有 unmatched/failed 曲目时保留 manifest 与产物供复查补下，只清已入库曲目
    album_complete = (bool(results) and all(r.action != "failed" for r in results)
                      and all(e.get("status") == "ok" for e in entries))
    ok_files = [e["file"] for e, r in zip(ok_entries, results)
                if r.action in ("linked", "copied", "skipped", "tag_unsupported") and e.get("file")]
    from .cleanup import cleanup_task_dir
    cleanup_task_dir(str(src_dir), ok_files, complete=album_complete)
    return ArchiveResult(status=status, library_dir=str(album_dir), summary=summary,
                         tracks=results, errors=errors)


_COVER_TIMEOUT = 30.0  # 单曲封面下载超时


def _cover_mime(cover_bytes: bytes) -> str:
    """按魔数识别封面 MIME（jpeg/png/webp），供嵌图 tag 使用。"""
    if cover_bytes[:4] == b"\x89PNG":
        return "image/png"
    if cover_bytes[:4] == b"RIFF" and cover_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _cover_url_candidates(url: str) -> list[str]:
    """酷我图片 URL 生成降级候选：
    albumcover（专辑封面）500 尺寸在部分节点 404，120 恒可用；
    starheads（艺人头像）无 500/700，按 300→240→120 降级。"""
    m = re.match(r"https://img\d\.kuwo\.cn/star/(albumcover|starheads)/\d+(/.+)", url)
    if not m:
        return [url]
    kind, path = m.group(1), m.group(2)
    sizes = ("500", "120") if kind == "albumcover" else ("300", "240", "120")
    hosts = ("1", "3") if kind == "albumcover" else ("1", "4")
    return [f"https://img{h}.kuwo.cn/star/{kind}/{size}{path}" for size in sizes for h in hosts]


def _download_cover_bytes(url: str | None) -> bytes | None:
    """按 URL 下载封面字节；失败按候选降级重试，最终失败返回 None（不阻塞归档）。"""
    if not url:
        return None
    import httpx
    for u in _cover_url_candidates(url):
        try:
            r = httpx.get(u, timeout=_COVER_TIMEOUT, follow_redirects=True)
            r.raise_for_status()
            return r.content
        except Exception:
            continue
    return None


def _itunes_cover_fallback(title: str, artist: str) -> bytes | None:
    """iTunes 单曲封面兜底：cover_url 缺失/下载失败时按 曲名+艺人 查 iTunes，取 600x600 封面。"""
    try:
        import httpx
        from .itunes import SEARCH_URL, _TIMEOUT, _hi_res_cover
        term = f"{artist} {title}".strip()
        r = httpx.get(SEARCH_URL, params={"term": term, "entity": "song", "limit": 5},
                      timeout=_TIMEOUT)
        r.raise_for_status()
        for item in r.json().get("results", []):
            cover = _download_cover_bytes(_hi_res_cover(item.get("artworkUrl100")))
            if cover:
                return cover
    except Exception:
        pass
    return None


_ARTIST_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _img_suffix(data: bytes) -> str:
    """按魔数定图片扩展名（jpeg/png/webp）。"""
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _save_artist_image(artist_dir: Path, url: str | None) -> str | None:
    """艺人目录写 artist.{jpg,png,webp}（Navidrome 本地艺人头像约定，ArtistArtPriority 默认含 artist.*）。

    幂等：目录已有 artist.* 图片则跳过（用户手动放置的头像永远优先）；
    无 URL 或下载失败返回 None（不阻塞归档）。返回写入的文件名。
    """
    if not url:
        return None
    try:
        for p in artist_dir.iterdir():
            if p.is_file() and p.stem.lower() == "artist" and p.suffix.lower() in _ARTIST_IMG_EXTS:
                return None
    except Exception:
        pass
    data = _download_cover_bytes(url)
    if not data:
        return None
    artist_dir.mkdir(parents=True, exist_ok=True)
    name = f"artist{_img_suffix(data)}"
    (artist_dir / name).write_bytes(data)
    return name


def _summarize(results: list[ArchiveTrackResult]) -> tuple[str, dict[str, int], list[str]]:
    summary: dict[str, int] = {}
    for r in results:
        summary[r.action] = summary.get(r.action, 0) + 1
    failed = summary.get("failed", 0)
    status = "success" if failed == 0 else ("failed" if failed == len(results) and results else "partial")
    errors = [f"{r.title}: {r.error}" for r in results if r.action == "failed"]
    return status, summary, errors


def archive_tracks(task_id: str, library: str | None = None, overwrite: bool = False) -> ArchiveResult:
    """把单曲下载任务的产物归档进媒体库（同步，幂等）。

    目标结构：{库根}/{艺人}/{曲名.ext}；同名 .lrc 放旁边并嵌入 tag；
    不写序号类 tag，ALBUM 用候选专辑名，DATE 跳过；封面按 候选 cover_url（酷我源搜索时已拼接兜底）
    → 酷我节点/尺寸降级 → iTunes 单曲封面 的顺序获取，均失败则不嵌图（不阻塞归档）。
    另按候选 artist_img_url 在艺人目录写 artist.*（Navidrome 艺人头像，幂等，已有则跳过）。
    library 为命名库根（见 libraries 模块），留空用默认库。
    """
    root = Path(resolve_library_root(library))
    task = dl.get(task_id)
    if not task:
        raise LookupError(f"任务 {task_id} 不在内存中（服务重启后单曲任务无法归档，请重新下载）")

    results: list[ArchiveTrackResult] = []
    archived_files: list[str] = []  # 成功入库的源文件名（归档后清理用）
    for item in task.results:
        title = t2s(item.get("title") or "")
        artists = item.get("artists") or []
        artist_dir = _safe_name(t2s(artists[0])) if artists else "未知艺人"
        res = ArchiveTrackResult(title=title, action="")
        try:
            if not item.get("file"):
                res.action = "failed"
                res.error = "下载未落盘（无文件）"
                results.append(res)
                continue
            src = Path(item.get("save_dir") or "") / item["file"]
            ext = src.suffix.lstrip(".") or (item.get("ext") or "flac").lstrip(".")
            rel = f"{artist_dir}/{_safe_name(title)}.{ext}"
            target = root / rel
            res.target = rel
            if target.exists() and not overwrite:
                res.action = "skipped"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.unlink()
                try:
                    os.link(src, target)
                    res.action = "linked"
                except OSError:
                    shutil.copy2(src, target)
                    res.action = "copied"
                if target.suffix.lstrip(".").lower() in _TAGGABLE_EXTS:
                    _break_link_if_needed(target)
                    # sidecar 歌词：嵌入 tag 并复制到目标旁
                    lyric_text = None
                    lrc_src = src.with_suffix(".lrc")
                    if lrc_src.exists():
                        lyric_text = lrc_src.read_text(encoding="utf-8", errors="ignore").strip() or None
                        shutil.copy2(lrc_src, target.with_suffix(".lrc"))
                    cover_bytes = (_download_cover_bytes(item.get("cover_url"))
                                   or _itunes_cover_fallback(title, artist_dir))
                    _write_tags(target, title, artist_dir, t2s(item.get("album") or ""),
                                cover_bytes=cover_bytes,
                                lyric_text=lyric_text)
                else:
                    res.action = "tag_unsupported"
        except Exception as e:  # 单曲失败不中断整体
            res.action = "failed"
            res.error = str(e)
        results.append(res)
        if res.action in ("linked", "copied", "skipped", "tag_unsupported") and item.get("file"):
            archived_files.append(item["file"])
        # 艺人头像：艺人目录无 artist.* 时按候选头像写一份（幂等，已有则跳过）
        _save_artist_image(root / artist_dir, item.get("artist_img_url"))

    status, summary, errors = _summarize(results)
    # 归档后自动清理下载产物：全成功整目录清，部分成功只清已入库曲目
    from .cleanup import cleanup_task_dir
    cleanup_task_dir(task.save_dir or "", archived_files,
                     complete=bool(results) and all(r.action != "failed" for r in results))
    return ArchiveResult(status=status, library_dir=str(root), summary=summary,
                         tracks=results, errors=errors)
