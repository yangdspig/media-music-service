"""FastAPI 入口：REST API 暴露核心能力。

可选 API Key 鉴权：config.yaml 里 api_key 非空时启用，校验 X-API-Key 头。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .schemas import AlbumDownloadRequest, AlbumInfo, AlbumSummary, ArchiveRequest, ArchiveResult, BackfillLyricsRequest, ChartSummary, CleanupLibraryRequest, DownloadRequest, DownloadTask, FnosPlaylistAppendRequest, FnosPlaylistRequest, MigrateSinglesRequest, ReplaceTrackRequest, SearchResponse, SourceInfo, TaskStatus, Track, TrackArchiveRequest
from . import album as album_svc
from . import archive as archive_svc
from . import download as dl
from . import fnos as fnos_svc
from . import backfill, charts as charts_svc, libraries, libops, meta, registry, storage, webconfig
from .playlist import parse_playlist
from .search import search

app = FastAPI(title="MediaMusicService", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    storage.init_db()
    from .cleanup import start_periodic_sweep
    start_periodic_sweep()  # 下载目录定期容量清理（按 config.yaml cleanup 段）
    from . import qqauth
    qqauth.start_keepalive()  # QQ 音乐登录态自动保活（按 config.yaml auth_refresh 段）


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


@app.get("/api/v1/charts", response_model=list[ChartSummary], dependencies=[Depends(auth)])
def api_charts(source: str | None = None) -> list[ChartSummary]:
    if source is not None and source not in charts_svc.SOURCES:
        raise HTTPException(status_code=400,
                            detail=f"不支持的榜单源：{source}（可选：{', '.join(charts_svc.SOURCES)}）")
    sources = [source] if source else list(charts_svc.SOURCES)
    charts, errors = [], []
    for s in sources:
        try:
            charts.extend(charts_svc.list_charts(s))
        except Exception as e:
            errors.append(f"{s}: {e}")
    if not charts and errors:
        raise HTTPException(status_code=502, detail=f"榜单目录获取失败：{'; '.join(errors)}")
    return charts


@app.get("/api/v1/charts/{source}/{chart_id}", response_model=list[Track], dependencies=[Depends(auth)])
def api_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]:
    try:
        return charts_svc.get_chart_tracks(source, chart_id, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"榜单曲目获取失败（{source}/{chart_id}）：{e}")


def _get_album_or_404(collection_id: str) -> AlbumInfo:
    try:
        return meta.get_album(collection_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"专辑元数据查询失败: {e}")


@app.get("/api/v1/libraries", dependencies=[Depends(auth)])
def api_libraries() -> list[dict]:
    return libraries.list_libraries()


# 注意：/albums/search 必须声明在 /albums/{collection_id} 之前，否则会被路径参数吃掉
@app.get("/api/v1/albums/search", response_model=list[AlbumSummary], dependencies=[Depends(auth)])
def api_album_search(keyword: str, artist: str | None = None, limit: int = 10) -> list[AlbumSummary]:
    try:
        return meta.search_albums(keyword=keyword, artist=artist, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"专辑搜索失败: {e}")


# 注意：/albums/archive 必须声明在 /albums/{collection_id} 之前，否则会被路径参数吃掉
@app.post("/api/v1/albums/archive", response_model=ArchiveResult, dependencies=[Depends(auth)])
def api_album_archive(req: ArchiveRequest) -> ArchiveResult:
    try:
        return archive_svc.archive_album(task_id=req.task_id, manifest_path=req.manifest_path,
                                         overwrite=req.overwrite, album_title=req.album_title,
                                         artist=req.artist, library=req.library,
                                         compilation=req.compilation)
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


@app.post("/api/v1/library/cleanup", dependencies=[Depends(auth)])
def api_library_cleanup(req: CleanupLibraryRequest) -> dict:
    try:
        return libops.cleanup_library(library=req.library, artist=req.artist, album=req.album,
                                      tracks=req.tracks, dry_run=req.dry_run, confirm=req.confirm)
    except (ValueError, LookupError, RuntimeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/library/migrate_singles", dependencies=[Depends(auth)])
def api_migrate_singles(req: MigrateSinglesRequest) -> dict:
    try:
        return libops.migrate_singles(library=req.library, target_library=req.target_library,
                                      artist=req.artist, dry_run=req.dry_run)
    except (ValueError, LookupError, RuntimeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/library/replace_track", dependencies=[Depends(auth)])
def api_replace_track(req: ReplaceTrackRequest) -> dict:
    try:
        return libops.replace_album_track(library=req.library, artist=req.artist, album=req.album,
                                          track=req.track, sources=req.sources, force=req.force,
                                          max_size_mb=req.max_size_mb)
    except (ValueError, LookupError, RuntimeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/v1/library/backfill_lyrics", dependencies=[Depends(auth)])
def api_backfill_lyrics(req: BackfillLyricsRequest) -> dict:
    try:
        return backfill.backfill_lyrics(library=req.library, artist=req.artist, album=req.album,
                                        sources=req.sources, limit=req.limit, dry_run=req.dry_run)
    except (ValueError, LookupError, RuntimeError, OSError) as e:
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
    if req.playlist and not req.library:
        raise HTTPException(status_code=400,
                            detail="playlist 必须搭配 library 使用（不入库的曲目飞牛音乐管不到）")
    if req.playlist and not settings.fnos_music:
        raise HTTPException(status_code=400,
                            detail="传了 playlist 但未配置 fnos_music（config.yaml）")
    try:
        return dl.submit(req.tracks, subdir=req.subdir, library=req.library,
                         max_size_mb=req.max_size_mb, playlist=req.playlist)
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


@app.post("/api/v1/auth/qq/refresh", dependencies=[Depends(auth)])
def api_qq_auth_refresh() -> dict:
    """手动触发一次 QQ 凭证刷新（强制，不看剩余有效期）。"""
    from . import qqauth
    return qqauth.keepalive_once(force=True)


# ---- Web 控制台：配置查看/热更新 + 系统状态 ----

@app.get("/api/v1/config", dependencies=[Depends(auth)])
def api_get_config() -> dict:
    """当前配置快照（敏感字段脱敏为掩码）。"""
    return webconfig.get_masked_config()


@app.put("/api/v1/config", dependencies=[Depends(auth)])
def api_put_config(body: dict[str, Any] = Body(...)) -> dict:
    """部分更新配置：掩码值跳过，回写 config.yaml（保注释），可变字段热应用。"""
    return webconfig.apply_update(body)


@app.get("/api/v1/system/status", dependencies=[Depends(auth)])
def api_system_status() -> dict:
    """系统状态聚合：源可用性 / 进行中任务 / 下载目录占用 / QQ 保活 / 飞牛连通性。

    全部字段允许部分失败降级（单项异常只标 error，不影响其他项）。
    """
    status: dict[str, Any] = {}
    # 各源可用性
    try:
        srcs = registry.list_sources()
        status["sources"] = {
            "total": len(srcs),
            "available": sum(1 for s in srcs if s["available"]),
            "unavailable": [{"name": s["name"], "note": s["note"]} for s in srcs if not s["available"]],
        }
    except Exception as e:
        status["sources"] = {"error": str(e)}
    # 进行中任务数
    try:
        tasks = dl.list_tasks(limit=1000)
        active = [t for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING)]
        status["tasks"] = {"active": len(active), "tracked": len(tasks),
                           "active_ids": [t.task_id for t in active]}
    except Exception as e:
        status["tasks"] = {"error": str(e)}
    # 下载目录占用（对照 cleanup.max_size_gb 阈值）
    try:
        root = Path(settings.download_root)
        total = sum(f.stat().st_size for f in root.rglob("*")
                    if f.is_file() and not f.is_symlink()) if root.is_dir() else 0
        size_gb = total / 1024 ** 3
        status["download_dir"] = {"path": str(root), "size_gb": round(size_gb, 3),
                                  "max_size_gb": settings.cleanup.max_size_gb,
                                  "over_threshold": size_gb > settings.cleanup.max_size_gb}
    except Exception as e:
        status["download_dir"] = {"error": str(e)}
    # QQ 保活状态（状态文件在 db_path 同目录；文件不存在视为未配置）
    try:
        from . import qqauth
        state_path = Path(settings.db_path).parent / "qq_auth_state.json"
        if not state_path.exists():
            status["qq_auth"] = {"configured": False}
        else:
            st = qqauth._load_state() or {}
            cred = st.get("credential") or {}
            createtime = int(cred.get("musickey_createtime") or 0)
            expires_in = int(cred.get("key_expires_in") or 0) or qqauth._DEFAULT_KEY_EXPIRES_IN
            expires_at = createtime + expires_in if createtime else None
            expired = bool(st.get("expired"))
            status["qq_auth"] = {"configured": True, "expired": expired,
                                 "expires_at": expires_at,
                                 "valid": bool(expires_at and not expired
                                               and expires_at > time.time())}
    except Exception as e:
        status["qq_auth"] = {"error": str(e)}
    # 飞牛连通性（配置了则试调 list_playlists，异常标 false 不抛出）
    try:
        if not settings.fnos_music:
            status["fnos_music"] = {"configured": False, "reachable": False}
        else:
            try:
                fnos_svc.list_playlists()
                reachable = True
            except Exception:
                reachable = False
            status["fnos_music"] = {"configured": True, "reachable": reachable}
    except Exception as e:
        status["fnos_music"] = {"error": str(e)}
    return status


# ---- 飞牛音乐歌单（原子管理 + 搜索；未配置 fnos_music → 400） ----

def _fnos_http(e: Exception) -> HTTPException:
    """fnos 异常 → HTTP：未配置 400 / 歌单不存在 404 / 其余（认证/API/网络）502。"""
    if isinstance(e, fnos_svc.FnosNotConfiguredError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, fnos_svc.FnosPlaylistNotFound):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=502, detail=f"飞牛音乐接口调用失败: {e}")


def _fnos_task_paths(task_id: str) -> list[str]:
    """取单曲任务成功入库曲目的容器路径：幂等重跑 archive_tracks 拿 target（任务须在内存）。"""
    task = dl.get(task_id)
    if not task:
        raise LookupError(f"任务 {task_id} 不在内存中（服务重启后请改用 paths 入参）")
    if not task.library:
        raise ValueError(f"任务 {task_id} 下载时未指定 library，无入库曲目，请改用 paths 入参")
    from .archive import archive_tracks, archived_container_paths
    return archived_container_paths(archive_tracks(task_id, library=task.library))


@app.get("/api/v1/fnos/playlists", dependencies=[Depends(auth)])
def api_fnos_playlists() -> list[dict]:
    try:
        return fnos_svc.list_playlists()
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/playlists/{name}/tracks", dependencies=[Depends(auth)])
def api_fnos_playlist_tracks(name: str) -> dict:
    try:
        return fnos_svc.playlist_detail(name)
    except Exception as e:
        raise _fnos_http(e)


@app.post("/api/v1/fnos/playlists", dependencies=[Depends(auth)])
def api_fnos_sync_playlist(req: FnosPlaylistRequest) -> dict:
    try:
        paths = list(req.paths or [])
        if req.task_id:
            paths.extend(_fnos_task_paths(req.task_id))
        return fnos_svc.sync_playlist(req.name, paths)
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _fnos_http(e)


@app.post("/api/v1/fnos/playlists/{name}/tracks", dependencies=[Depends(auth)])
def api_fnos_append_tracks(name: str, req: FnosPlaylistAppendRequest) -> dict:
    if not req.paths and not req.guids:
        raise HTTPException(status_code=400, detail="paths 与 guids 至少传其一")
    try:
        return fnos_svc.append_tracks(name, container_paths=req.paths, guids=req.guids)
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/search", dependencies=[Depends(auth)])
def api_fnos_search(q: str = "") -> dict:  # q 缺省/空白统一 400（规格口径，不走 FastAPI 422）
    if not q.strip():
        raise HTTPException(status_code=400, detail="q 不能为空")
    try:
        return fnos_svc.search_suggest(q)
    except Exception as e:
        raise _fnos_http(e)


@app.get("/api/v1/fnos/search/tracks", dependencies=[Depends(auth)])
def api_fnos_search_tracks(q: str = "", limit: int = 50) -> dict:
    if not q.strip():
        raise HTTPException(status_code=400, detail="q 不能为空")
    try:
        return fnos_svc.search_tracks(q, limit=limit)
    except Exception as e:
        raise _fnos_http(e)


# ---- SPA 静态托管：web/dist 存在时挂在 /（API 路由已先注册，/api/* 不受影响） ----

class _SPAStaticFiles(StaticFiles):
    """history 路由回退：静态文件未命中时回落 index.html。"""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if (_WEB_DIST / "index.html").is_file():
    app.mount("/", _SPAStaticFiles(directory=str(_WEB_DIST), html=True), name="web")
