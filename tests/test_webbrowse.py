"""只读浏览接口的边界：命名库、越界与符号链接、任务清单归属。"""
import json

import pytest
from fastapi.testclient import TestClient

from app import download
from app.config import settings
from app.main import app
from app.schemas import DownloadTask


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app)


def test_entries_pagination_and_named_library(client, tmp_path, monkeypatch):
    (tmp_path / "艺人").mkdir()
    (tmp_path / "歌.flac").write_bytes(b"audio")
    (tmp_path / "cover.jpg").write_bytes(b"cover")
    monkeypatch.setattr(settings, "extra_library_roots", {"singles": str(tmp_path)})
    r = client.get("/api/v1/library/entries", params={"library": "singles", "limit": 1})
    assert r.status_code == 200
    assert r.json()["entries"][0]["path"] == "艺人"
    assert r.json()["has_more"] is True
    r = client.get("/api/v1/library/entries", params={"library": "singles", "offset": 1})
    audio = next(e for e in r.json()["entries"] if e["audio"])
    assert audio["size_bytes"] == 5
    assert r.json()["has_more"] is False
    assert client.get("/api/v1/library/entries?library=unknown").status_code == 404


@pytest.mark.parametrize("path", ["../", "艺人/../../", "/etc", "link"])
def test_entries_reject_escape_and_symlink(client, tmp_path, monkeypatch, path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(settings, "library_root", str(root))
    r = client.get("/api/v1/library/entries", params={"path": path})
    assert r.status_code == 400
    assert client.get("/api/v1/library/entries").json()["entries"] == []


def test_manifest_membership_and_lifecycle(client, tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    task = DownloadTask(task_id="test", save_dir=str(tmp_path), manifest_path=str(path))
    monkeypatch.setattr(download, "get", lambda task_id: task if task_id == "test" else None)
    assert client.get("/api/v1/downloads/missing/manifest").status_code == 404
    assert client.get("/api/v1/downloads/test/manifest").status_code == 404
    payload = {"task_id": "test", "tracks": [{"match": {"score": 0.92}}]}
    path.write_text(json.dumps(payload))
    assert client.get("/api/v1/downloads/test/manifest").json() == payload
    path.write_text(json.dumps({"task_id": "another"}))
    assert client.get("/api/v1/downloads/test/manifest").status_code == 400
    path.write_text("invalid")
    assert client.get("/api/v1/downloads/test/manifest").status_code == 400
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text(json.dumps(payload))
    task.manifest_path = str(outside / "manifest.json")
    assert client.get("/api/v1/downloads/test/manifest").status_code == 400


def test_browse_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "private")
    for route in ["/api/v1/library/entries", "/api/v1/downloads/test/manifest"]:
        assert client.get(route).status_code == 401
