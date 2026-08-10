"""FastAPI 入口：REST API 暴露核心能力。

可选 API Key 鉴权：config.yaml 里 api_key 非空时启用，校验 X-API-Key 头。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings
from .schemas import DownloadRequest, DownloadTask, SearchResponse, SourceInfo, Track
from . import download as dl
from . import registry, storage
from .playlist import parse_playlist
from .search import search

app = FastAPI(title="MediaMusicService", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()


async def auth(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


@app.get("/api/v1/health")
def health() -> dict:
    return {"ok": True, "musicdl": _musicdl_version()}


def _musicdl_version() -> str:
    try:
        import musicdl
        return musicdl.__version__
    except Exception:
        return "unknown"


@app.get("/api/v1/sources", response_model=list[SourceInfo], dependencies=[Depends(auth)])
def get_sources() -> list[dict]:
    return registry.list_sources()


@app.get("/api/v1/search", response_model=SearchResponse, dependencies=[Depends(auth)])
def api_search(keyword: str, sources: str | None = None, limit: int = 20) -> SearchResponse:
    src_list = [s.strip() for s in sources.split(",")] if sources else None
    tracks, failed = search(keyword=keyword, sources=src_list, limit=limit)
    return SearchResponse(keyword=keyword, total=len(tracks), tracks=tracks, failed_sources=failed)


@app.get("/api/v1/playlist", response_model=list[Track], dependencies=[Depends(auth)])
def api_playlist(url: str, source: str | None = None) -> list[Track]:
    try:
        return parse_playlist(url=url, source=source)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"歌单解析失败: {e}")


@app.post("/api/v1/downloads", response_model=DownloadTask, dependencies=[Depends(auth)])
def api_submit(req: DownloadRequest) -> DownloadTask:
    if not req.tracks:
        raise HTTPException(status_code=400, detail="tracks 不能为空")
    return dl.submit(req.tracks, subdir=req.subdir)


@app.get("/api/v1/downloads/{task_id}", response_model=DownloadTask, dependencies=[Depends(auth)])
def api_task(task_id: str) -> DownloadTask:
    t = dl.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    return t


@app.post("/api/v1/downloads/{task_id}/cancel", dependencies=[Depends(auth)])
def api_cancel(task_id: str) -> dict:
    return {"canceled": dl.cancel(task_id)}


@app.get("/api/v1/downloads", dependencies=[Depends(auth)])
def api_list(limit: int = 20) -> list[dict]:
    return [t.model_dump() for t in dl.list_tasks(limit)]


@app.get("/api/v1/history", dependencies=[Depends(auth)])
def api_history(limit: int = 50) -> list[dict]:
    return storage.list_history(limit)
