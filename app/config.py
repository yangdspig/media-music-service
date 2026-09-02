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


class MCPConfig(BaseModel):
    """MCP 适配器配置（仅供 mcp_adapter.py 使用，核心服务忽略）。

    优先级：环境变量（MUSIC_MCP_* / MUSIC_SERVICE_URL / MUSIC_SERVICE_API_KEY）> 本配置 > 默认值。
    api_key 直接复用顶层 api_key 项，无需单独配置。
    """
    transport: str = "stdio"  # stdio：本地 Agent 直接拉起；http：远程 Agent
    host: str = "0.0.0.0"
    port: int = 8766
    service_url: str = "http://127.0.0.1:8765"  # 核心 REST 服务地址；docker 部署改为 http://music-service:8765


class CleanupConfig(BaseModel):
    """下载目录清理规则：归档后自动清理 + 定期容量清理。

    安全红线：下载根目录可能混有用户私人文件，清理采用白名单制，
    只删服务自建产物（DB 记录的任务目录、musicdl 源缓存目录），详见 app/cleanup.py。
    """
    after_archive: bool = True  # 归档成功后清理该任务的下载产物
    periodic: bool = True  # 定期扫描下载目录占用
    interval_s: int = 21600  # 扫描周期（秒），默认 6 小时
    max_size_gb: float = 10  # 下载目录占用阈值（GB），超过才触发清理
    keep_hours: float = 24  # 近 N 小时的任务目录保护期（不删）


class AuthRefreshConfig(BaseModel):
    """QQ 音乐登录态自动保活。状态文件写 db_path 同目录的 qq_auth_state.json。"""
    enabled: bool = True  # 总开关
    interval_s: int = 3600  # 检查周期（秒），默认 1 小时；剩余有效期 <24h 才实际刷新


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
    mcp: MCPConfig = MCPConfig()  # MCP 适配器配置（仅 mcp_adapter.py 读取，核心服务不使用）
    cleanup: CleanupConfig = CleanupConfig()  # 下载目录清理规则
    auth_refresh: AuthRefreshConfig = AuthRefreshConfig()  # QQ cookie 自动保活


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    path = Path(os.environ.get("MUSIC_SERVICE_CONFIG", str(_DEFAULT_CONFIG_PATH)))
    data = _load_yaml(path)
    # 兼容嵌套的 server: 段（host/port），顶层同名字段优先
    server = data.pop("server", None) or {}
    data = {**server, **data}
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
