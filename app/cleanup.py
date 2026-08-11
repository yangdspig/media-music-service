"""下载目录清理：归档后自动清理 + 定期容量清理（白名单制）。

安全红线：下载根目录可能混有用户私人文件，清理只碰两类服务自建产物——
1) DB tasks 表记录过的任务 save_dir；2) musicdl 各源缓存目录（*MusicClient/）。
所有删除前都校验解析路径必须位于下载根目录之内，其余文件一律不碰。
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

_SOURCE_CACHE_SUFFIX = "MusicClient"  # musicdl 各源搜索缓存目录名后缀


def _under_download_root(p: Path) -> bool:
    """路径解析后必须严格位于下载根目录之内（防脏数据/软链越界）。"""
    try:
        root = Path(settings.download_root).resolve()
        resolved = p.resolve()
        return resolved != root and resolved.is_relative_to(root)
    except Exception:
        return False


def _tree_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
    except Exception:
        return 0


def _rmtree(p: Path) -> int:
    """删除目录树，返回释放字节数（尽力统计）。"""
    size = _tree_size(p)
    shutil.rmtree(p, ignore_errors=True)
    return size


def cleanup_task_dir(save_dir: str, ok_files: list[str], complete: bool) -> None:
    """归档成功后清理该任务的下载产物（受 cleanup.after_archive 开关控制）。

    complete=True（归档全部成功）：整目录删除（含 manifest/pkl 等全部产物）；
    否则仅删除 ok_files 列出的音频及同名 .lrc，保留 manifest 与失败曲目供复查补下。
    """
    if not settings.cleanup.after_archive:
        return
    d = Path(save_dir)
    if not save_dir or not d.is_dir() or not _under_download_root(d):
        return
    if complete and ok_files:
        freed = _rmtree(d)
        logger.info("归档后清理：删除任务目录 %s（释放 %.1fMB）", d, freed / 1024 / 1024)
        return
    for name in ok_files:
        for f in (d / name, (d / name).with_suffix(".lrc")):
            try:
                if f.is_file() and _under_download_root(f):
                    f.unlink()
            except Exception:
                pass


def _sweep_once() -> None:
    """单次容量扫描：下载根目录占用超阈值时，按创建时间从旧到新删白名单目录至阈值 80%。

    白名单：DB tasks 表记录的任务目录（受 keep_hours 保护期限制）
    + musicdl 源缓存目录（纯搜索缓存，不受保护期限制，直接清）。
    """
    cfg = settings.cleanup
    root = Path(settings.download_root)
    if not root.is_dir():
        return
    total = _tree_size(root)
    limit = cfg.max_size_gb * 1024 ** 3
    if total <= limit:
        return
    logger.info("定期清理触发：下载目录 %.2fGB 超过阈值 %.2fGB", total / 1024 ** 3, cfg.max_size_gb)
    cutoff = time.time() - cfg.keep_hours * 3600
    from . import storage
    candidates: list[tuple[float, Path, bool]] = []  # (创建时间, 路径, 是否源缓存)
    for save_dir, created in storage.list_task_dirs():
        d = Path(save_dir or "")
        if d.is_dir() and _under_download_root(d):
            candidates.append((created or d.stat().st_mtime, d, False))
    for d in root.iterdir():
        if d.is_dir() and d.name.endswith(_SOURCE_CACHE_SUFFIX) and _under_download_root(d):
            candidates.append((d.stat().st_mtime, d, True))
    candidates.sort(key=lambda x: x[0])  # 从旧到新
    target = limit * 0.8  # 滞回：清到阈值的 80%，防反复触发
    for ts, d, is_cache in candidates:
        if total <= target:
            break
        if not is_cache and ts > cutoff:
            continue  # 保护期内的任务目录不删
        freed = _rmtree(d)
        total -= freed
        logger.info("定期清理：删除 %s（释放 %.1fMB）", d, freed / 1024 / 1024)


def _sweep_loop() -> None:
    while True:
        time.sleep(settings.cleanup.interval_s)
        try:
            _sweep_once()
        except Exception:
            logger.exception("下载目录定期清理异常")


def start_periodic_sweep() -> None:
    """服务启动时调用：按配置开启定期清理后台线程。"""
    if not settings.cleanup.periodic:
        return
    threading.Thread(target=_sweep_loop, daemon=True, name="download-dir-sweeper").start()
