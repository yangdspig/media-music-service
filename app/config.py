"""配置加载：单一 config.yaml + 环境变量覆盖。

纯内网自用定位下，配置项保持最小集合：
- 服务监听、下载根目录、线程数
- 可选 API Key（为空即关闭鉴权）
- 各源 cookies（按需配置，不配的源自动标记不可用）
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class SourceConfig(BaseModel):
    """单个源的覆盖配置。"""
    enabled: bool = True
    search_cookies: str | dict | None = None
    download_cookies: str | dict | None = None
    parse_cookies: str | dict | None = None
    quark_cookies: str | dict | None = None
    extra: dict[str, Any] = {}


class Settings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8765
    download_root: str = "./downloads"
    db_path: str = "./data/music_service.db"
    num_threads: int = 5
    download_timeout_s: int = 300  # 单源下载超时保护，防止 musicdl 内部无限等待
    api_key: str | None = None  # 为空则不启用鉴权
    library_root: str | None = None  # 媒体库根目录（archive_album 归档目标）；为空则归档不可用
    extra_library_roots: dict[str, str] = {}  # 命名附加库根（如 {"singles": "/singles"}），归档可按库名选择目标（白名单，调用方不传裸路径）
    max_size_mb: float | None = None  # 单文件体积上限（MB）：超出则专辑匹配跳过该候选、单曲下载拒绝；0/空不限；接口传参优先
    archive_comment: str = "yangds整理"  # 归档时统一写入的 COMMENT tag
    default_sources: list[str] = [
        "MiguMusicClient", "NeteaseMusicClient", "QQMusicClient",
        "KuwoMusicClient", "QianqianMusicClient",
    ]
    sources: dict[str, SourceConfig] = {}


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    path = Path(os.environ.get("MUSIC_SERVICE_CONFIG", str(_DEFAULT_CONFIG_PATH)))
    data = _load_yaml(path)
    s = Settings(**data)
    # 环境变量覆盖
    if os.environ.get("MUSIC_SERVICE_API_KEY"):
        s.api_key = os.environ["MUSIC_SERVICE_API_KEY"]
    if os.environ.get("MUSIC_SERVICE_DOWNLOAD_ROOT"):
        s.download_root = os.environ["MUSIC_SERVICE_DOWNLOAD_ROOT"]
    if os.environ.get("MUSIC_SERVICE_LIBRARY_ROOT"):
        s.library_root = os.environ["MUSIC_SERVICE_LIBRARY_ROOT"]
    # 相对路径统一锚定到项目根（config.yaml 所在目录），避免随进程 CWD 漂移
    project_root = path.resolve().parent
    for attr in ("download_root", "db_path", "library_root"):
        v = getattr(s, attr)
        if v and not os.path.isabs(v):
            setattr(s, attr, str(project_root / v))
    s.extra_library_roots = {
        k: (v if os.path.isabs(v) else str(project_root / v))
        for k, v in (s.extra_library_roots or {}).items()
    }
    # 确保目录存在
    Path(s.download_root).mkdir(parents=True, exist_ok=True)
    Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
    return s


def effective_max_size_mb(override: float | None) -> float | None:
    """体积上限解析：接口传参（>0）优先，否则用配置；0/空均视为不限。"""
    v = override if override and override > 0 else settings.max_size_mb
    return v if v and v > 0 else None


settings = load_settings()
