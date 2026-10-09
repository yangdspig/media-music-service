"""极简持久化：SQLite 记录下载任务与历史。

M1 只做"可追溯"，不做复杂查询；任务实时状态仍在内存中维护（见 download.py），
这里只落终态与落盘文件清单，供服务重启后回看历史。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import settings


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                   task_id TEXT PRIMARY KEY,
                   status TEXT, total INTEGER, completed INTEGER, failed INTEGER,
                   save_dir TEXT, message TEXT,
                   results TEXT, errors TEXT,
                   created_at REAL, updated_at REAL
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS files (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   task_id TEXT, source TEXT, title TEXT, artists TEXT,
                   save_path TEXT, ext TEXT, size_bytes INTEGER, created_at REAL
               )"""
        )


def upsert_task(t: dict) -> None:
    now = time.time()
    with _conn() as c:
        c.execute(
            """INSERT INTO tasks (task_id,status,total,completed,failed,save_dir,message,results,errors,created_at,updated_at)
               VALUES (:task_id,:status,:total,:completed,:failed,:save_dir,:message,:results,:errors,:ts,:ts)
               ON CONFLICT(task_id) DO UPDATE SET
                 status=excluded.status,total=excluded.total,completed=excluded.completed,
                 failed=excluded.failed,save_dir=excluded.save_dir,message=excluded.message,
                 results=excluded.results,errors=excluded.errors,updated_at=excluded.updated_at""",
            {
                "task_id": t["task_id"], "status": t["status"], "total": t.get("total", 0),
                "completed": t.get("completed", 0), "failed": t.get("failed", 0),
                "save_dir": t.get("save_dir"), "message": t.get("message", ""),
                "results": json.dumps(t.get("results", []), ensure_ascii=False),
                "errors": json.dumps(t.get("errors", []), ensure_ascii=False), "ts": now,
            },
        )


def record_file(task_id: str, track: dict, save_path: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO files (task_id,source,title,artists,save_path,ext,size_bytes,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, track.get("source"), track.get("title"), ",".join(track.get("artists", [])),
             save_path, track.get("ext"), track.get("size_bytes"), time.time()),
        )


def list_task_dirs() -> list[tuple[str, float]]:
    """全部任务记录过的 save_dir 及最早创建时间（下载目录清理的白名单来源）。"""
    with _conn() as c:
        rows = c.execute(
            "SELECT save_dir, MIN(created_at) AS ts FROM tasks WHERE save_dir IS NOT NULL GROUP BY save_dir"
        ).fetchall()
    return [(r["save_dir"], r["ts"]) for r in rows]


def list_history(limit: int = 50, order_by: str = "created_at") -> list[dict]:
    # 终态记录最后一次更新时间即任务完成时间；未完成任务排在已完成任务之后。
    orders = {
        "created_at": "created_at DESC, task_id DESC",
        "completed_at": "CASE WHEN status IN ('success', 'failed', 'canceled') "
                        "THEN updated_at END DESC, created_at DESC, task_id DESC",
    }
    if order_by not in orders:
        raise ValueError(f"不支持的任务排序字段: {order_by}")
    with _conn() as c:
        rows = c.execute(f"SELECT * FROM tasks ORDER BY {orders[order_by]} LIMIT ?", (limit,)).fetchall()
        # 旧版 results 未记录大小；使用同任务的文件记录补齐，无需改写历史。
        task_ids = [r["task_id"] for r in rows]
        files = c.execute(
            f"SELECT task_id,source,title,save_path,size_bytes FROM files "
            f"WHERE task_id IN ({','.join('?' for _ in task_ids)}) ORDER BY id DESC",
            task_ids,
        ).fetchall() if task_ids else []
    file_sizes: dict[tuple, int] = {}
    for file in files:
        if file["size_bytes"] is not None and file["save_path"]:
            key = (file["task_id"], file["source"], Path(file["save_path"]).name)
            file_sizes.setdefault(key, file["size_bytes"])
    out = []
    for r in rows:
        d = dict(r)
        d["results"] = json.loads(d.get("results") or "[]")
        for result in d["results"]:
            if result.get("size_bytes") is None:
                name = result.get("file") or result.get("save_path")
                key = (d["task_id"], result.get("source"), Path(name).name) if name else None
                result["size_bytes"] = file_sizes.get(key)
        d["errors"] = json.loads(d.get("errors") or "[]")
        d["completed_at"] = d["updated_at"] if d["status"] in ("success", "failed", "canceled") else None
        out.append(d)
    return out
