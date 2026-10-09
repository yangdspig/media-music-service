"""Web 页面使用的只读目录与专辑匹配清单查询。"""
from __future__ import annotations

import json
from pathlib import Path

from . import download
from .libraries import resolve_library_root


def list_entries(library: str | None, path: str = "", offset: int = 0,
                 limit: int = 100) -> dict:
    """仅列出命名库内一层目录；不跟随符号链接、不返回文件内容。"""
    root = Path(resolve_library_root(library)).resolve()
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("path 必须是库内相对路径，不能包含 ..")
    directory = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("不能浏览符号链接目录")
    if not directory.resolve().is_relative_to(root):
        raise ValueError("目录不在指定媒体库内")
    if not directory.is_dir():
        raise LookupError("媒体库目录不存在或尚未挂载")
    children = sorted((p for p in directory.iterdir()
                       if not p.is_symlink() and (p.is_dir() or p.is_file())),
                      key=lambda p: (not p.is_dir(), p.name.casefold()))
    entries = []
    for child in children[offset:offset + limit]:
        is_dir = child.is_dir()
        entries.append({
            "name": child.name, "path": child.relative_to(root).as_posix(),
            "type": "directory" if is_dir else "file",
            "audio": not is_dir and child.suffix.lower() in download._AUDIO_EXTS,
            "size_bytes": None if is_dir else child.stat().st_size,
        })
    return {"library": library or "default", "root": str(root),
            "path": relative.as_posix() if relative.parts else "",
            "entries": entries, "total": len(children), "offset": offset,
            "has_more": offset + limit < len(children)}


def get_manifest(task_id: str) -> dict:
    """只读取内存任务登记的 manifest，不接受调用者传入文件路径。"""
    task = download.get(task_id)
    if task is None:
        raise LookupError("任务不存在，服务重启后内存任务会丢失")
    if not task.manifest_path or not task.save_dir:
        raise LookupError("专辑匹配清单尚未生成")
    directory = Path(task.save_dir).resolve()
    manifest = Path(task.manifest_path)
    if (manifest.is_symlink() or manifest.name != "manifest.json"
            or manifest.resolve().parent != directory):
        raise ValueError("匹配清单不在任务下载目录内")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise LookupError("匹配清单已清理或不存在") from e
    except (UnicodeError, json.JSONDecodeError) as e:
        raise ValueError("匹配清单格式无效") from e
    if not isinstance(data, dict) or data.get("task_id") != task_id:
        raise ValueError("匹配清单与任务不一致")
    return data
