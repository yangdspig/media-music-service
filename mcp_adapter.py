"""MCP 适配器：把核心 REST 服务封装为 MCP 工具，供 Agent 调用。

设计要点（按设计方案 M2）：
- 薄客户端，不直接依赖 musicdl 与 app 包，所有逻辑通过 HTTP 调用核心服务；
- 默认 stdio 传输（本地 Agent 直接拉起），也可用 HTTP（远程 Agent）；
- 工具与核心服务能力一一对应：搜索 / 歌单解析 / 提交下载 / 查询状态 / 列出可用源。

配置来源（优先级：环境变量 > config.yaml 的 mcp 段 > 默认值）：
- 配置文件路径默认取脚本旁的 config.yaml，可用 MUSIC_SERVICE_CONFIG 指定；
- api_key 复用 config.yaml 顶层 api_key 项，与核心服务保持一致，无需单独配置；
- 环境变量 MUSIC_SERVICE_URL / MUSIC_SERVICE_API_KEY / MUSIC_MCP_TRANSPORT /
  MUSIC_MCP_HOST / MUSIC_MCP_PORT 仍可覆盖，便于无配置文件的 stdio 场景。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP


def _file_config() -> dict[str, Any]:
    """读 config.yaml 的 mcp 段与顶层 api_key；文件缺失/解析失败时静默回退默认。"""
    path = Path(os.environ.get("MUSIC_SERVICE_CONFIG",
                               str(Path(__file__).resolve().parent / "config.yaml")))
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg = dict(data.get("mcp") or {})
        cfg.setdefault("api_key", data.get("api_key"))
        return cfg
    except Exception:
        return {}


_CFG = _file_config()

BASE_URL = os.environ.get("MUSIC_SERVICE_URL") or _CFG.get("service_url") or "http://127.0.0.1:8765"
API_KEY = os.environ.get("MUSIC_SERVICE_API_KEY") or _CFG.get("api_key") or ""

mcp = FastMCP("media-music")


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=_headers(), timeout=120)


@mcp.tool()
def list_sources() -> dict:
    """列出全部音乐源及其能力（是否支持歌单、是否需要 cookies、当前是否可用）。"""
    with _client() as c:
        r = c.get("/api/v1/sources")
        r.raise_for_status()
        items = r.json()
    return {"total": len(items), "available": [s for s in items if s["available"]],
            "unavailable": [{"name": s["name"], "note": s["note"]} for s in items if not s["available"]]}


@mcp.tool()
def list_libraries() -> dict:
    """列出全部归档目标库（命名库根）：默认库 default + config.yaml 配置的命名附加库。

    归档/下载时传 library 参数选择目标库（如单曲库 "singles"），留空用默认库。
    """
    with _client() as c:
        r = c.get("/api/v1/libraries")
        r.raise_for_status()
        libs = r.json()
    return {"total": len(libs), "libraries": libs}


@mcp.tool()
def search_tracks(keyword: str, sources: str | None = None, limit: int = 20) -> dict:
    """按关键词搜索歌曲或有声读物。

    Args:
        keyword: 搜索词（歌名/歌手/专辑）
        sources: 逗号分隔的源名，如 "NeteaseMusicClient,QQMusicClient"；留空用默认五源
        limit: 返回条数上限
    """
    params: dict[str, Any] = {"keyword": keyword, "limit": limit}
    if sources:
        params["sources"] = sources
    with _client() as c:
        r = c.get("/api/v1/search", params=params)
        r.raise_for_status()
        data = r.json()
    # 精简返回，避免把 musicdl 原始大字段塞给 Agent；下载时只需回传 id，服务端按缓存补全
    tracks = [{
        "id": t["id"], "source": t["source"], "title": t["title"],
        "artists": t["artists"], "album": t["album"], "ext": t["ext"],
        "quality": t["quality"], "size_bytes": t["size_bytes"],
        "duration_s": t["duration_s"], "cover_url": t["cover_url"],
    } for t in data["tracks"]]
    return {"keyword": data["keyword"], "total": data["total"],
            "failed_sources": data["failed_sources"], "tracks": tracks}


@mcp.tool()
def parse_playlist(url: str, source: str | None = None) -> dict:
    """解析歌单 URL，返回歌单内全部曲目（仅对支持歌单的源有效）。"""
    params: dict[str, Any] = {"url": url}
    if source:
        params["source"] = source
    with _client() as c:
        r = c.get("/api/v1/playlist", params=params)
        r.raise_for_status()
        tracks = r.json()
    return {"total": len(tracks),
            "tracks": [{"id": t["id"], "source": t["source"], "title": t["title"],
                        "artists": t["artists"], "album": t["album"], "ext": t["ext"]} for t in tracks]}


@mcp.tool()
def submit_download(tracks: list[dict], subdir: str | None = None, library: str | None = None,
                    max_size_mb: float | None = None) -> dict:
    """提交下载任务（异步）。

    Args:
        tracks: 待下载曲目列表。推荐每项只传 id 字段（取自 search_tracks / parse_playlist
            返回项的 id），如 [{"id": "KuwoMusicClient:594551679"}]，服务端按搜索缓存自动
            补全下载上下文；缓存有效期 1 小时，过期或服务重启后需重新搜索。
            也兼容直接回传 search_tracks 的完整返回项（含 raw），但没必要。
        subdir: 下载根目录下的子目录名，留空则按"时间戳_首曲名"自动组织
        library: 目标库名（可选，见 list_libraries）；传入则下载完成后自动归档到该库，
            单曲入库结构为 {库根}/{艺人}/{曲名.ext}（专辑请用 download_album + archive_album）
        max_size_mb: 单文件体积上限（MB，可选）；>0 时超限曲目跳过且优先于服务端配置，0/空不限
    Returns:
        task_id 等，用 get_download_status 轮询进度，以其 status/errors 为最终结果。
    """
    payload: dict[str, Any] = {"tracks": tracks}
    if subdir:
        payload["subdir"] = subdir
    if library:
        payload["library"] = library
    if max_size_mb:
        payload["max_size_mb"] = max_size_mb
    with _client() as c:
        r = c.post("/api/v1/downloads", json=payload)
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "total": t["total"], "save_dir": t["save_dir"]}


@mcp.tool()
def get_download_status(task_id: str) -> dict:
    """查询下载任务状态与进度（单曲与专辑任务通用；专辑任务结果里带 manifest_path）。"""
    with _client() as c:
        r = c.get(f"/api/v1/downloads/{task_id}")
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "completed": t["completed"],
            "total": t["total"], "failed": t["failed"], "current": t["current"],
            "message": t["message"], "save_dir": t["save_dir"],
            "manifest_path": t.get("manifest_path"),
            "results": t["results"], "errors": t["errors"]}


@mcp.tool()
def search_albums(keyword: str, artist: str | None = None, limit: int = 10) -> dict:
    """按专辑名搜索专辑（iTunes 优先；覆盖不足时自动回退网易云/QQ，尽量补充中文简介）。

    Args:
        keyword: 专辑名
        artist: 艺人名（可选，叠加可提高准确度）
        limit: 返回条数上限
    Returns:
        专辑列表，含 collection_id（供 get_album_info / download_album 使用；
        iTunes 专辑为纯数字，中文源专辑带 netease:/qq: 前缀）、曲目数、发行日期、
        高清封面 URL、description（简介，可为空）、meta_source（元数据来源）。
    """
    params: dict[str, Any] = {"keyword": keyword, "limit": limit}
    if artist:
        params["artist"] = artist
    with _client() as c:
        r = c.get("/api/v1/albums/search", params=params)
        r.raise_for_status()
        albums = r.json()
    return {"total": len(albums), "albums": albums}


@mcp.tool()
def get_album_info(collection_id: str) -> dict:
    """获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面、简介等。

    Args:
        collection_id: search_albums 返回的 collection_id（支持 netease:/qq: 前缀的中文源专辑；
            iTunes id 在其各 storefront 均无曲目时自动回退中文源整体接管）
    """
    with _client() as c:
        r = c.get(f"/api/v1/albums/{collection_id}")
        r.raise_for_status()
        return r.json()


@mcp.tool()
def download_album(collection_id: str, sources: str | None = None, subdir: str | None = None,
                   album_title: str | None = None, artist: str | None = None,
                   max_size_mb: float | None = None) -> dict:
    """专辑整单下载（异步）：服务端逐曲搜索匹配、按曲目序号命名落盘，并产出 manifest.json。

    Args:
        collection_id: search_albums 返回的 collection_id（支持 netease:/qq: 前缀）
        sources: 逗号分隔的源名（可选，留空用默认五源）
        subdir: 下载根目录下的子目录名（可选，默认"{艺人} - {专辑}"）
        album_title: 显示用专辑名覆盖（可选；iTunes 专辑名为罗马音/拼音时建议传入中文名，
            会写入 manifest 供归档作为目录名与 ALBUM tag）
        artist: 显示用艺人名覆盖（可选，同上）
        max_size_mb: 单文件体积上限（MB，可选）；>0 时超限候选不参与匹配且优先于服务端配置，0/空不限
    Returns:
        task_id / save_dir，用 get_download_status 轮询进度；完成后
        manifest_path 指向的 manifest.json 含逐曲匹配分数、落盘文件与失败原因，供复核。
    """
    payload: dict[str, Any] = {}
    if sources:
        payload["sources"] = [s.strip() for s in sources.split(",") if s.strip()]
    if subdir:
        payload["subdir"] = subdir
    if album_title:
        payload["album_title"] = album_title
    if artist:
        payload["artist"] = artist
    if max_size_mb:
        payload["max_size_mb"] = max_size_mb
    with _client() as c:
        r = c.post(f"/api/v1/albums/{collection_id}/download", json=payload)
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "total": t["total"], "save_dir": t["save_dir"]}


@mcp.tool()
def archive_album(task_id: str | None = None, manifest_path: str | None = None, overwrite: bool = False,
                  album_title: str | None = None, artist: str | None = None,
                  library: str | None = None) -> dict:
    """把专辑下载产物归档进媒体库（同步）：硬链接/复制入库、写 tag、嵌封面歌词、生成 album_info.txt。

    目录名与 tag 的专辑名/艺人名按解析链确定：显式参数 > manifest display_*
    > 自动推断（国内源候选多数表决，仅在 iTunes 原名为罗马音时生效）> iTunes 原名。

    Args:
        task_id: download_album 返回的任务 ID（服务未重启时可用，推荐）
        manifest_path: manifest.json 的绝对路径（服务重启后用这个）
        overwrite: 目标已存在时是否覆盖重建；默认 False（幂等跳过）
        album_title: 显示用专辑名覆盖（可选，最高优先级；自动推断仍不对时用它兜底）
        artist: 显示用艺人名覆盖（可选，最高优先级）
        library: 目标库名（可选，见 list_libraries；留空用默认库）
    Returns:
        归档结果：library_dir（库内专辑目录）、逐曲 action（linked/copied/skipped/failed）、
        summary 计数、errors。目录结构为 {库根}/{艺人}/{专辑}/，多 Disc 用 CD1/CD2 子目录。
    """
    payload: dict[str, Any] = {"overwrite": overwrite}
    if task_id:
        payload["task_id"] = task_id
    if manifest_path:
        payload["manifest_path"] = manifest_path
    if album_title:
        payload["album_title"] = album_title
    if artist:
        payload["artist"] = artist
    if library:
        payload["library"] = library
    with _client() as c:
        r = c.post("/api/v1/albums/archive", json=payload)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def archive_tracks(task_id: str, library: str | None = None, overwrite: bool = False) -> dict:
    """把单曲下载任务的产物归档进媒体库（同步）：硬链接/复制入库、写 tag、嵌封面歌词。

    Args:
        task_id: submit_download 返回的任务 ID（服务重启后内存任务丢失，需重新下载）
        library: 目标库名（可选，见 list_libraries；留空用默认库）
        overwrite: 目标已存在时是否覆盖重建；默认 False（幂等跳过）
    Returns:
        归档结果：逐曲 action、summary 计数、errors。
        入库结构为 {库根}/{艺人}/{曲名.ext}，同名 .lrc 放旁边；不写曲目序号。
    """
    payload: dict[str, Any] = {"task_id": task_id, "overwrite": overwrite}
    if library:
        payload["library"] = library
    with _client() as c:
        r = c.post("/api/v1/tracks/archive", json=payload)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def cleanup_library(artist: str, album: str | None = None, tracks: list[str] | None = None,
                    library: str | None = None, dry_run: bool = False) -> dict:
    """清理媒体库中的专辑或曲目文件；存放文件的目录变空时一并清理（不留空目录）。

    Args:
        artist: 艺人名（库内一级目录）
        album: 专辑名（可选；留空则删除整个艺人目录）
        tracks: 要删除的曲目（可选，序号如 "3" 或曲名；留空则删除整个专辑）
        library: 库名（可选，见 list_libraries；留空用默认库）
        dry_run: True 时只报告将删除的项，不实际删除（建议先跑一遍确认范围）
    Returns:
        deleted_files（已删文件）、removed_dirs（已清目录）、errors。
    """
    payload: dict[str, Any] = {"artist": artist, "dry_run": dry_run}
    if album:
        payload["album"] = album
    if tracks:
        payload["tracks"] = tracks
    if library:
        payload["library"] = library
    with _client() as c:
        r = c.post("/api/v1/library/cleanup", json=payload)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def migrate_singles(library: str | None = None, target_library: str = "singles",
                    artist: str | None = None, dry_run: bool = False) -> dict:
    """扫描专辑库中只有一个音频文件的专辑目录（单曲专辑），迁移到 singles 库。

    迁移后清除曲目序号类 tag（保留专辑名/封面/歌词），同名 .lrc 一并移动，
    原专辑目录与空艺人目录自动清理；目标已存在同名文件则跳过。

    Args:
        library: 源库名（可选，见 list_libraries；留空用默认库）
        target_library: 目标库名（默认 singles）
        artist: 限定单个艺人（可选；留空扫描整个源库）
        dry_run: True 时只报告将迁移的项，不实际迁移（建议先跑一遍确认范围）
    Returns:
        migrated（from/to 列表）、skipped（含原因）、errors。
    """
    payload: dict[str, Any] = {"target_library": target_library, "dry_run": dry_run}
    if library:
        payload["library"] = library
    if artist:
        payload["artist"] = artist
    with _client() as c:
        r = c.post("/api/v1/library/migrate_singles", json=payload)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def replace_album_track(artist: str, album: str, track: str, library: str | None = None,
                        sources: str | None = None, force: bool = False,
                        max_size_mb: float | None = None) -> dict:
    """重新搜索专辑中指定曲目，用规格更高、更好的版本替换（同步）。

    新候选音质高于现有文件（无损 > 320k > 其他）时才替换，否则返回 action=kept；
    force=True 强制替换。替换后序号/专辑/艺人/日期沿用旧 tag，封面沿用专辑 cover。

    Args:
        artist: 艺人名（库内一级目录）
        album: 专辑名（库内二级目录）
        track: 曲目序号（如 "3"）或曲名
        library: 库名（可选，见 list_libraries；留空用默认库）
        sources: 逗号分隔的源名（可选，留空用默认五源）
        force: 新候选音质不高于现有版本也强制替换
        max_size_mb: 单文件体积上限（MB，可选）
    Returns:
        action（replaced/kept/unmatched/failed）、old/new 规格信息、error。
    """
    payload: dict[str, Any] = {"artist": artist, "album": album, "track": track, "force": force}
    if library:
        payload["library"] = library
    if sources:
        payload["sources"] = [s.strip() for s in sources.split(",") if s.strip()]
    if max_size_mb:
        payload["max_size_mb"] = max_size_mb
    with _client() as c:
        r = c.post("/api/v1/library/replace_track", json=payload)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    transport = os.environ.get("MUSIC_MCP_TRANSPORT") or _CFG.get("transport") or "stdio"
    if transport == "stdio":
        mcp.run()
    else:
        # 供远程 Agent 通过 HTTP 连接
        host = os.environ.get("MUSIC_MCP_HOST") or _CFG.get("host") or "0.0.0.0"
        port = int(os.environ.get("MUSIC_MCP_PORT") or _CFG.get("port") or 8766)
        mcp.run(transport="http", host=host, port=port)
