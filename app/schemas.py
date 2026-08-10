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
