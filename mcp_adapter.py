"""MCP 适配器：把核心 REST 服务封装为 MCP 工具，供 Agent 调用。

设计要点（按设计方案 M2）：
- 薄客户端，不直接依赖 musicdl，所有逻辑通过 HTTP 调用核心服务；
- 默认 stdio 传输（本地 Agent 直接拉起），也可用 SSE/HTTP（远程 Agent）；
- 工具与核心服务能力一一对应：搜索 / 歌单解析 / 提交下载 / 查询状态 / 列出可用源。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastmcp import FastMCP

BASE_URL = os.environ.get("MUSIC_SERVICE_URL", "http://127.0.0.1:8765")
API_KEY = os.environ.get("MUSIC_SERVICE_API_KEY", "")

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
    # 精简返回，避免把 musicdl 原始大字段塞给 Agent；下载时按 id 回取
    tracks = [{
        "id": t["id"], "source": t["source"], "title": t["title"],
        "artists": t["artists"], "album": t["album"], "ext": t["ext"],
        "quality": t["quality"], "size_bytes": t["size_bytes"],
        "duration_s": t["duration_s"], "cover_url": t["cover_url"],
        "raw": t["raw"],  # 下载时需原样回传给服务端
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
                        "artists": t["artists"], "album": t["album"], "ext": t["ext"],
                        "raw": t["raw"]} for t in tracks]}


@mcp.tool()
def submit_download(tracks: list[dict], subdir: str | None = None) -> dict:
    """提交下载任务（异步）。

    Args:
        tracks: 完整 track 对象列表，须包含 raw 字段（直接取自 search_tracks / parse_playlist 的返回项）
        subdir: 下载根目录下的子目录名，留空则按"时间戳_首曲名"自动组织
    Returns:
        task_id 等，可用 get_download_status 轮询进度。
    """
    payload: dict[str, Any] = {"tracks": tracks}
    if subdir:
        payload["subdir"] = subdir
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
    """按专辑名搜索专辑（iTunes 官方元数据）。

    Args:
        keyword: 专辑名
        artist: 艺人名（可选，叠加可提高准确度）
        limit: 返回条数上限
    Returns:
        专辑列表，含 collection_id（供 get_album_info / download_album 使用）、
        曲目数、发行日期、高清封面 URL。
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
    """获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面等。

    Args:
        collection_id: search_albums 返回的 collection_id
    """
    with _client() as c:
        r = c.get(f"/api/v1/albums/{collection_id}")
        r.raise_for_status()
        return r.json()


@mcp.tool()
def download_album(collection_id: str, sources: str | None = None, subdir: str | None = None) -> dict:
    """专辑整单下载（异步）：服务端逐曲搜索匹配、按曲目序号命名落盘，并产出 manifest.json。

    Args:
        collection_id: search_albums 返回的 collection_id
        sources: 逗号分隔的源名（可选，留空用默认五源）
        subdir: 下载根目录下的子目录名（可选，默认"{艺人} - {专辑}"）
    Returns:
        task_id / save_dir，用 get_download_status 轮询进度；完成后
        manifest_path 指向的 manifest.json 含逐曲匹配分数、落盘文件与失败原因，供复核。
    """
    payload: dict[str, Any] = {}
    if sources:
        payload["sources"] = [s.strip() for s in sources.split(",") if s.strip()]
    if subdir:
        payload["subdir"] = subdir
    with _client() as c:
        r = c.post(f"/api/v1/albums/{collection_id}/download", json=payload)
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "total": t["total"], "save_dir": t["save_dir"]}


@mcp.tool()
def archive_album(task_id: str | None = None, manifest_path: str | None = None, overwrite: bool = False) -> dict:
    """把专辑下载产物归档进媒体库（同步）：硬链接/复制入库、写 tag、嵌封面歌词、生成 album_info.txt。

    Args:
        task_id: download_album 返回的任务 ID（服务未重启时可用，推荐）
        manifest_path: manifest.json 的绝对路径（服务重启后用这个）
        overwrite: 目标已存在时是否覆盖重建；默认 False（幂等跳过）
    Returns:
        归档结果：library_dir（库内专辑目录）、逐曲 action（linked/copied/skipped/failed）、
        summary 计数、errors。目录结构为 {library_root}/{艺人}/{专辑}/，多 Disc 用 CD1/CD2 子目录。
    """
    payload: dict[str, Any] = {"overwrite": overwrite}
    if task_id:
        payload["task_id"] = task_id
    if manifest_path:
        payload["manifest_path"] = manifest_path
    with _client() as c:
        r = c.post("/api/v1/albums/archive", json=payload)
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    transport = os.environ.get("MUSIC_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        # 供远程 Agent 通过 HTTP/SSE 连接
        host = os.environ.get("MUSIC_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MUSIC_MCP_PORT", "8766"))
        mcp.run(transport="http", host=host, port=port)
