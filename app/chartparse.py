"""榜单异步解析：受限后台线程、真实逐曲进度、内存结果和协作式取消。"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from . import charts
from .schemas import ChartParseTask, Track
from .search import cache_tracks

_MAX_ACTIVE = 4
_MAX_RETAINED = 32
_RESULT_TTL_S = 3600
_LOCK = threading.Lock()


class ParseBusyError(RuntimeError):
    pass


class _Canceled(Exception):
    pass


@dataclass
class _Job:
    state: ChartParseTask
    tracks: list[Track] = field(default_factory=list)
    canceled: threading.Event = field(default_factory=threading.Event)
    active: bool = True


_JOBS: dict[str, _Job] = {}


def _prune(reserve_slot: bool = False) -> None:
    """调用方持锁；只清理已结束任务，运行线程始终占用并发额度。"""
    now = time.time()
    finished = sorted((job for job in _JOBS.values() if not job.active),
                      key=lambda job: job.state.finished_at or job.state.created_at)
    for job in finished:
        if now - (job.state.finished_at or job.state.created_at) > _RESULT_TTL_S or len(_JOBS) > _MAX_RETAINED - int(reserve_slot):
            _JOBS.pop(job.state.task_id, None)


def _snapshot(job: _Job) -> ChartParseTask:
    state = job.state.model_copy(deep=True)
    state.elapsed_s = round(max(0, (state.finished_at or time.time()) - state.created_at), 1)
    return state


def _lookup(task_id: str) -> _Job:
    _prune()
    if task_id not in _JOBS:
        raise LookupError("解析任务不存在或已过期，请重新解析")
    return _JOBS[task_id]


def submit(source: str, chart_id: str, limit: int | None = None) -> ChartParseTask:
    if source not in charts.SOURCES:
        raise ValueError(f"不支持的榜单源：{source}")
    if not chart_id.isascii() or not chart_id.isdecimal() or len(chart_id) > 32:
        raise ValueError("榜单 id 必须为数字")
    if limit is not None and limit < 1:
        raise ValueError("解析数量必须大于 0")
    if source == "qq" and limit is not None:
        limit = min(limit, 100)
    with _LOCK:
        _prune(reserve_slot=True)
        if sum(job.active for job in _JOBS.values()) >= _MAX_ACTIVE:
            raise ParseBusyError("正在解析的榜单较多，请等待当前解析结束后重试")
        now = time.time()
        state = ChartParseTask(task_id=uuid.uuid4().hex, source=source, chart_id=chart_id,
                               limit=limit, created_at=now, updated_at=now)
        job = _Job(state)
        _JOBS[state.task_id] = job
    try:
        threading.Thread(target=_run, args=(job,), daemon=True, name=f"chart-{state.task_id[:8]}").start()
    except Exception:
        with _LOCK:
            _JOBS.pop(state.task_id, None)
        raise
    return get(state.task_id)


def _run(job: _Job) -> None:
    def update(changes: dict) -> None:
        with _LOCK:
            if job.canceled.is_set():
                raise _Canceled()
            job.state.status = "running"
            for key, value in changes.items():
                setattr(job.state, key, value)
            job.state.updated_at = time.time()

    try:
        tracks = charts.get_chart_tracks(job.state.source, job.state.chart_id,
                                         limit=job.state.limit, progress=update)
        with _LOCK:
            if job.canceled.is_set():
                raise _Canceled()
            job.tracks = tracks
            job.state.status = "success"
            job.state.stage = "completed"
            job.state.current = None
            job.state.message = f"解析完成：可下载 {len(tracks)} 首，跳过/失败 {job.state.skipped} 首"
    except Exception as exc:
        with _LOCK:
            if job.canceled.is_set() or isinstance(exc, _Canceled):
                job.state.status = "canceled"
                job.state.message = "解析已停止"
            else:
                job.state.status = "failed"
                job.state.error = str(exc)
                job.state.message = "解析失败，请重试"
            job.state.current = None
    finally:
        with _LOCK:
            job.state.finished_at = job.state.updated_at = time.time()
            job.active = False


def get(task_id: str) -> ChartParseTask:
    with _LOCK:
        return _snapshot(_lookup(task_id))


def get_tracks(task_id: str) -> list[Track]:
    with _LOCK:
        job = _lookup(task_id)
        if job.state.status != "success":
            raise ValueError("解析尚未成功完成，暂时无法读取曲目结果")
        tracks = list(job.tracks)
    cache_tracks(tracks)  # 用户读取结果时续期，提交下载仍可只传 id。
    return tracks


def cancel(task_id: str) -> ChartParseTask:
    with _LOCK:
        job = _lookup(task_id)
        if job.active and job.state.status in ("pending", "running", "canceling"):
            job.canceled.set()
            job.state.status = "canceling"
            job.state.message = "正在停止解析，等待当前曲目请求结束"
            job.state.updated_at = time.time()
        return _snapshot(job)
