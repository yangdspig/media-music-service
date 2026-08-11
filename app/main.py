"""FastAPI 入口：REST API 暴露核心能力。

可选 API Key 鉴权：config.yaml 里 api_key 非空时启用，校验 X-API-Key 头。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException

from .config import settings
from .schemas import AlbumDownloadRequest, AlbumInfo, AlbumSummary, ArchiveRequest, ArchiveResult, DownloadRequest, DownloadTask, SearchResponse, SourceInfo, Track, TrackArchiveRequest
from . import album as album_svc
from . import archive as archive_svc
from . import download as dl
from . import itunes, libraries, registry, storage
from .playlist import parse_playlist
from .search import search

app = FastAPI(title="MediaMusicService", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()
    from .cleanup import start_periodic_sweep
    start_periodic_sweep()  # 下载目录定期容量清理（按 config.yaml cleanup 段）


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


def _get_album_or_404(collection_id: str) -> AlbumInfo:
    try:
        return itunes.get_album(collection_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"iTunes 查询失败: {e}")


@app.get("/api/v1/libraries", dependencies=[Depends(auth)])
def api_libraries() -> list[dict]:
    return libraries.list_libraries()


# 注意：/albums/search 必须声明在 /albums/{collection_id} 之前，否则会被路径参数吃掉
@app.get("/api/v1/albums/search", response_model=list[AlbumSummary], dependencies=[Depends(auth)])
def api_album_search(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    try:
        return itunes.search_albums(keyword=keyword, artist=artist, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"iTunes 搜索失败: {e}")


# 注意：/albums/archive 必须声明在 /albums/{collection_id} 之前，否则会被路径参数吃掉
@app.post("/api/v1/albums/archive", response_model=ArchiveResult, dependencies=[Depends(auth)])
def api_album_archive(req: ArchiveRequest) -> ArchiveResult:
    try:
        return archive_svc.archive_album(task_id=req.task_id, manifest_path=req.manifest_path,
                                         overwrite=req.overwrite, album_title=req.album_title,
                                         artist=req.artist, library=req.library)
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/tracks/archive", response_model=ArchiveResult, dependencies=[Depends(auth)])
def api_tracks_archive(req: TrackArchiveRequest) -> ArchiveResult:
    try:
        return archive_svc.archive_tracks(req.task_id, library=req.library, overwrite=req.overwrite)
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/v1/albums/{collection_id}", response_model=AlbumInfo, dependencies=[Depends(auth)])
def api_album_info(collection_id: str) -> AlbumInfo:
    return _get_album_or_404(collection_id)


@app.post("/api/v1/albums/{collection_id}/download", response_model=DownloadTask, dependencies=[Depends(auth)])
def api_album_download(collection_id: str, req: AlbumDownloadRequest) -> DownloadTask:
    album = _get_album_or_404(collection_id)
    if not album.tracks:
        raise HTTPException(status_code=400, detail="专辑曲目表为空，无法下载")
    return album_svc.submit_album_download(album, sources=req.sources, subdir=req.subdir,
                                           album_title=req.album_title, artist=req.artist,
                                           max_size_mb=req.max_size_mb)


@app.post("/api/v1/downloads", response_model=DownloadTask, dependencies=[Depends(auth)])
def api_submit(req: DownloadRequest) -> DownloadTask:
    if not req.tracks:
        raise HTTPException(status_code=400, detail="tracks 不能为空")
    try:
        return dl.submit(req.tracks, subdir=req.subdir, library=req.library,
                         max_size_mb=req.max_size_mb)
    except (ValueError, LookupError, RuntimeError) as e:  # 全部超限 / 未知库名 / 未配置默认库
        raise HTTPException(status_code=400, detail=str(e))


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
