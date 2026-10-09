"""最近任务按完成时间排序，并在排序后截取记录。"""
import pytest
from fastapi.testclient import TestClient

from app import storage
from app.config import settings
from app.main import app


@pytest.fixture
def task_history(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "history.db"))
    monkeypatch.setattr(settings, "api_key", "")
    storage.init_db()

    def save(task_id, timestamp, status):
        monkeypatch.setattr(storage.time, "time", lambda: timestamp)
        storage.upsert_task({"task_id": task_id, "status": status})

    # ID、创建顺序与完成顺序故意不一致；早创建的长任务最后完成。
    save("000-long", 10, "running")
    for index in range(10):
        save(f"fff-short-{index}", 20 + index, "success")
    save("000-long", 40, "success")
    save("zzz-running", 50, "running")
    save("zzz-pending", 60, "pending")


def test_history_completion_order_before_limit(task_history):
    rows = storage.list_history(limit=8, order_by="completed_at")
    assert [row["task_id"] for row in rows] == ["000-long"] + [f"fff-short-{i}" for i in range(9, 2, -1)]
    assert rows[0]["created_at"] == 10
    assert rows[0]["completed_at"] == 40
    assert all(row["completed_at"] is not None for row in rows)
    full = storage.list_history(limit=20, order_by="completed_at")
    assert [row["task_id"] for row in full[-2:]] == ["zzz-pending", "zzz-running"]
    assert all(row["completed_at"] is None for row in full[-2:])
    # 未传参数的历史接口仍按创建时间排序。
    assert storage.list_history(limit=1)[0]["task_id"] == "zzz-pending"


def test_history_route_completion_order(task_history):
    client = TestClient(app)
    response = client.get("/api/v1/history?order_by=completed_at&limit=1")
    assert response.status_code == 200
    assert response.json()[0]["task_id"] == "000-long"
    assert client.get("/api/v1/history?order_by=invalid").status_code == 422
