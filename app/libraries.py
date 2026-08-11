"""命名库根：白名单式多媒体库根目录的解析与发现。

背景：需要把专辑与单曲分库存放（如 library 放专辑、singles 放单曲）。
调用方只传库名（library="singles"）而非裸路径，防止任意路径写入；
库根白名单来自 config.yaml：library_root 为默认库，extra_library_roots 为命名附加库。
"""
from __future__ import annotations

from .config import settings


def list_libraries() -> list[dict]:
    """列出全部可用库：默认库（default）+ 命名附加库。"""
    libs: list[dict] = []
    if settings.library_root:
        libs.append({"name": "default", "root": settings.library_root, "default": True})
    for name, root in (settings.extra_library_roots or {}).items():
        libs.append({"name": name, "root": root, "default": False})
    return libs


def resolve_library_root(name: str | None = None) -> str:
    """按库名解析库根绝对路径；name 为空时用默认库（library_root）。

    未配置默认库抛 RuntimeError；库名未知抛 LookupError（提示可用库名）。
    """
    if not name or name == "default":
        if not settings.library_root:
            raise RuntimeError("未配置 library_root（媒体库根目录），归档不可用；请在 config.yaml 配置并挂载媒体库卷")
        return settings.library_root
    roots = settings.extra_library_roots or {}
    if name in roots:
        return roots[name]
    available = (["default"] if settings.library_root else []) + list(roots.keys())
    raise LookupError(f"未知库名: {name}（可用: {', '.join(available) or '无，请先在 config.yaml 配置'}）")
