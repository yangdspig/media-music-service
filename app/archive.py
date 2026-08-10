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
import shutil
from pathlib import Path
from typing import Any, Optional

from . import download as dl
from .album import _safe_name, infer_display_names, t2s
from .config import settings
from .schemas import ArchiveResult, ArchiveTrackResult

_TAGGABLE_EXTS = {"flac", "mp3"}


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


def _write_tags(path: Path, entry: dict, artist: str, album_title: str, date: str,
                disc_total: int, disc_track_total: int,
                cover_bytes: bytes | None, lyric_text: str | None) -> None:
    """按库约定重写 tag 并嵌封面/歌词（仅 flac/mp3）。artist/album_title 为解析后的显示名。"""
    ext = path.suffix.lstrip(".").lower()
    title = t2s(entry.get("title") or "")
    track_no = f"{entry['track']}/{disc_track_total}"
    disc_no = f"{entry['disc']}/{disc_total}" if disc_total > 1 else None
    comment = settings.archive_comment

    if ext == "flac":
        from mutagen.flac import FLAC, Picture
        audio = FLAC(path)
        # 白名单重写：清掉平台水印等杂项后统一写入
        keep = {"ARTIST", "ALBUMARTIST", "ALBUM", "TITLE", "DATE",
                "TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL", "COMMENT", "LYRICS"}
        for key in list(audio.keys()):
            if key.upper() not in keep:
                del audio[key]
        audio["ARTIST"] = artist
        audio["ALBUMARTIST"] = artist
        audio["ALBUM"] = album_title
        audio["TITLE"] = title
        if date:
            audio["DATE"] = date
        audio["TRACKNUMBER"] = track_no
        audio["TRACKTOTAL"] = str(disc_track_total)
        if disc_no:
            audio["DISCNUMBER"] = disc_no
            audio["DISCTOTAL"] = str(disc_total)
        audio["COMMENT"] = comment
        if lyric_text:
            audio["LYRICS"] = lyric_text
        if cover_bytes:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = "image/png" if cover_bytes[:4] == b"\x89PNG" else "image/jpeg"
            pic.desc = "cover"
            pic.data = cover_bytes
            audio.add_picture(pic)
        audio.save()
    elif ext == "mp3":
        from mutagen.id3 import APIC, COMM, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, USLT, ID3
        try:
            audio = ID3(path)
        except Exception:
            audio = ID3()
        audio.delall("TPE1"); audio.add(TPE1(encoding=3, text=artist))
        audio.delall("TPE2"); audio.add(TPE2(encoding=3, text=artist))
        audio.delall("TALB"); audio.add(TALB(encoding=3, text=album_title))
        audio.delall("TIT2"); audio.add(TIT2(encoding=3, text=title))
        if date:
            audio.delall("TDRC"); audio.add(TDRC(encoding=3, text=date))
        audio.delall("TRCK"); audio.add(TRCK(encoding=3, text=track_no))
        if disc_no:
            audio.delall("TPOS"); audio.add(TPOS(encoding=3, text=disc_no))
        audio.delall("COMM"); audio.add(COMM(encoding=3, lang="eng", desc="", text=comment))
        if lyric_text:
            audio.delall("USLT"); audio.add(USLT(encoding=3, lang="eng", desc="", text=lyric_text))
        if cover_bytes:
            audio.delall("APIC")
            mime = "image/png" if cover_bytes[:4] == b"\x89PNG" else "image/jpeg"
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
    """生成 album_info.txt（简介暂缺：iTunes 无此字段，待网易云/QQ 源补充）。"""
    orig_title = album.get("title") or ""
    orig_artists = " / ".join(album.get("artists") or [])
    lines = [
        f"专辑：{display_title}" + (f"（iTunes 原名：{orig_title}）" if orig_title != display_title else ""),
        f"艺人：{display_artist}" + (f"（iTunes 原名：{orig_artists}）" if orig_artists and orig_artists != display_artist else ""),
        f"发行日期：{(album.get('release_date') or '')[:10]}",
        f"流派：{album.get('genre') or ''}",
        f"元数据来源：iTunes (collection {album.get('collection_id')}, storefront {album.get('storefront')})",
        "",
        "曲目表：",
    ]
    for e in entries:
        dur = e.get("duration_s")
        dur_s = f" ({int(dur // 60)}:{int(dur % 60):02d})" if dur else ""
        prefix = f"CD{e['disc']} " if len({x['disc'] for x in entries}) > 1 else ""
        lines.append(f"{prefix}{e['track']:02d}. {t2s(e.get('title'))}{dur_s}")
    lines += ["", "（专辑简介暂缺：iTunes 不提供简介字段，待后续网易云/QQ 元数据补充）"]
    (album_dir / "album_info.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def archive_album(task_id: str | None = None, manifest_path: str | None = None,
                  overwrite: bool = False, album_title: str | None = None,
                  artist: str | None = None) -> ArchiveResult:
    """按 manifest 把专辑下载产物归档进媒体库（同步，幂等）。

    目录名与 tag 用的专辑名/艺人名按解析链确定：
    显式 album_title/artist 参数 > manifest display_* > 自动推断 > iTunes 原名。
    """
    if not settings.library_root:
        raise RuntimeError("未配置 library_root（媒体库根目录），归档不可用；请在 config.yaml 配置并挂载媒体库卷")
    manifest, src_dir = _load_manifest(task_id, manifest_path)
    album = manifest.get("album") or {}
    entries = manifest.get("tracks") or []
    ok_entries = [e for e in entries if e.get("status") == "ok" and e.get("file")]
    disp_title, disp_artist = _resolve_names(album, ok_entries, album_title, artist)
    album_dir = Path(settings.library_root) / _safe_name(disp_artist) / _safe_name(disp_title)
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
                    _write_tags(target, entry, disp_artist, disp_title,
                                (album.get("release_date") or "")[:10],
                                disc_total, disc_track_total, cover_bytes, lyric_text)
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

    summary: dict[str, int] = {}
    for r in results:
        summary[r.action] = summary.get(r.action, 0) + 1
    failed = summary.get("failed", 0)
    status = "success" if failed == 0 else ("failed" if failed == len(results) and results else "partial")
    errors = [f"{r.title}: {r.error}" for r in results if r.action == "failed"]
    return ArchiveResult(status=status, library_dir=str(album_dir), summary=summary,
                         tracks=results, errors=errors)
