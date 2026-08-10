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
    """查询下载任务状态与进度。"""
    with _client() as c:
        r = c.get(f"/api/v1/downloads/{task_id}")
        r.raise_for_status()
        t = r.json()
    return {"task_id": t["task_id"], "status": t["status"], "completed": t["completed"],
            "total": t["total"], "failed": t["failed"], "current": t["current"],
            "message": t["message"], "save_dir": t["save_dir"],
            "results": t["results"], "errors": t["errors"]}


if __name__ == "__main__":
    transport = os.environ.get("MUSIC_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        # 供远程 Agent 通过 HTTP/SSE 连接
        host = os.environ.get("MUSIC_MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MUSIC_MCP_PORT", "8766"))
        mcp.run(transport="http", host=host, port=port)
