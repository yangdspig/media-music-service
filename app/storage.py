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


def list_history(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["results"] = json.loads(d.get("results") or "[]")
        d["errors"] = json.loads(d.get("errors") or "[]")
        out.append(d)
    return out
