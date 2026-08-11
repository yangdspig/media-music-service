"""统一的 API 数据模型。

Track 是对 musicdl SongInfo 的标准化封装，服务对外只暴露这个结构，
避免把 musicdl 内部字段透传给客户端（MoviePilot 插件 / MCP）。
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class Track(BaseModel):
    """标准化音轨（歌曲或有声读物单集）。"""
    id: str = Field(description="全局唯一标识：{source}:{identifier}")
    source: str = Field(description="来源客户端名，如 NeteaseMusicClient")
    title: str = Field(description="歌曲/节目名")
    artists: list[str] = Field(default_factory=list, description="歌手/主播列表")
    album: Optional[str] = Field(default=None, description="专辑名")
    duration_s: Optional[float] = Field(default=None, description="时长（秒）")
    quality: Optional[str] = Field(default=None, description="音质描述，如 lossless/320k")
    ext: Optional[str] = Field(default=None, description="文件格式，如 flac/mp3/m4a")
    size_bytes: Optional[int] = Field(default=None, description="文件大小（字节）")
    cover_url: Optional[str] = Field(default=None, description="封面图 URL")
    lyric: Optional[str] = Field(default=None, description="歌词文本（若有）")
    raw: dict[str, Any] = Field(default_factory=dict, description="musicdl 原始 SongInfo dict，供下载时回传")


class SearchResponse(BaseModel):
    keyword: str
    total: int
    tracks: list[Track]
    failed_sources: list[str] = Field(default_factory=list, description="本次搜索失败的源")


class SourceInfo(BaseModel):
    name: str
    category: str = Field(description="china / global / audiobook / aggregator / thirdparty")
    supports_search: bool = True
    supports_download: bool = True
    supports_playlist: bool = False
    needs_cookies: bool = False
    available: bool = True
    note: str = ""


class DownloadRequest(BaseModel):
    tracks: list[Track] = Field(description="待下载的音轨（通常来自 search/playlist 结果）")
    subdir: Optional[str] = Field(default=None, description="下载根目录下的子目录，默认按规则自动组织")
    library: Optional[str] = Field(default=None, description="目标库名（见 GET /api/v1/libraries）；传入则下载完成后自动归档到该库")
    max_size_mb: Optional[float] = Field(default=None, description="单文件体积上限（MB），超限曲目跳过；>0 才生效且优先于配置，0/空不限")


class AlbumSummary(BaseModel):
    """专辑摘要（iTunes 搜索结果项）。"""
    collection_id: str = Field(description="iTunes collectionId")
    title: str = Field(description="专辑名")
    artists: list[str] = Field(default_factory=list, description="艺人列表")
    release_date: Optional[str] = Field(default=None, description="发行日期（ISO）")
    track_count: int = Field(default=0, description="曲目数")
    cover_url: Optional[str] = Field(default=None, description="高清封面 URL（600x600）")
    genre: Optional[str] = Field(default=None, description="流派")


class AlbumTrack(BaseModel):
    """专辑内一首曲目（官方曲目表）。"""
    disc: int = Field(default=1, description="Disc 序号")
    track: int = Field(description="Disc 内序号")
    title: str
    artists: list[str] = Field(default_factory=list)
    duration_s: Optional[float] = Field(default=None, description="时长（秒）")


class AlbumInfo(AlbumSummary):
    """专辑详情：摘要 + 完整曲目表。"""
    tracks: list[AlbumTrack] = Field(default_factory=list, description="按 disc/track 排序的官方曲目表")
    storefront: Optional[str] = Field(default=None, description="实际命中曲目的 iTunes storefront（如 CN/HK/US）")


class AlbumDownloadRequest(BaseModel):
    sources: Optional[list[str]] = Field(default=None, description="参与匹配下载的源，留空用默认五源")
    subdir: Optional[str] = Field(default=None, description="下载根目录下的子目录，默认'{艺人} - {专辑}'")
    album_title: Optional[str] = Field(default=None, description="显示用专辑名覆盖（应对 iTunes 罗马音专辑名，写入 manifest 供归档使用）")
    artist: Optional[str] = Field(default=None, description="显示用艺人名覆盖")
    max_size_mb: Optional[float] = Field(default=None, description="单文件体积上限（MB），超限候选不参与匹配；>0 才生效且优先于配置，0/空不限")


class ArchiveRequest(BaseModel):
    """归档请求：task_id 与 manifest_path 必填其一（task_id 优先）。"""
    task_id: Optional[str] = Field(default=None, description="专辑下载任务 ID（取其 manifest_path）")
    manifest_path: Optional[str] = Field(default=None, description="manifest.json 路径（直接指定）")
    overwrite: bool = Field(default=False, description="目标已存在时是否覆盖重建；默认跳过（幂等）")
    album_title: Optional[str] = Field(default=None, description="显示用专辑名覆盖（最高优先级，影响目录名与 ALBUM tag）")
    artist: Optional[str] = Field(default=None, description="显示用艺人名覆盖（最高优先级，影响目录名与 ARTIST tag）")
    library: Optional[str] = Field(default=None, description="目标库名（命名库根，见 GET /api/v1/libraries）；留空用默认库")


class TrackArchiveRequest(BaseModel):
    """单曲归档请求：把单曲下载任务的产物归档进媒体库。"""
    task_id: str = Field(description="单曲下载任务 ID（其 results 须含落盘文件信息）")
    library: Optional[str] = Field(default=None, description="目标库名（命名库根，见 GET /api/v1/libraries）；留空用默认库")
    overwrite: bool = Field(default=False, description="目标已存在时是否覆盖重建；默认跳过（幂等）")


class ArchiveTrackResult(BaseModel):
    disc: int = 1
    track: int = 0
    title: str = ""
    target: Optional[str] = Field(default=None, description="库内相对路径（相对 library_root）")
    action: str = Field(description="linked / copied / skipped / failed / tag_unsupported")
    error: Optional[str] = None


class ArchiveResult(BaseModel):
    status: str = Field(description="success / partial / failed")
    library_dir: Optional[str] = Field(default=None, description="专辑入库目录（绝对路径）")
    summary: dict[str, int] = Field(default_factory=dict, description="按 action 计数")
    tracks: list[ArchiveTrackResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class DownloadTask(BaseModel):
    task_id: str
    status: str = TaskStatus.PENDING
    total: int = 0
    completed: int = 0
    failed: int = 0
    current: Optional[str] = Field(default=None, description="当前正在下载的曲目名")
    message: str = ""
    save_dir: Optional[str] = None
    results: list[dict[str, Any]] = Field(default_factory=list, description="成功项的落盘信息")
    errors: list[str] = Field(default_factory=list)
    manifest_path: Optional[str] = Field(default=None, description="专辑下载产出的 manifest.json 路径（仅专辑任务）")
    library: Optional[str] = Field(default=None, description="目标库名；单曲任务传入时下载完成后自动归档到该库")
