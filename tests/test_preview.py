"""试听必须鉴权、限定缓存曲目，并保持音频分段传输语义。"""
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import Track
from app.search import cache_tracks


@pytest.fixture
def track():
    item = Track(id="QQMusicClient:preview-test", source="QQMusicClient", title="试听曲目", ext="mp3",
                 raw={"download_url": "https://audio.example/song.mp3",
                      "default_download_headers": {"Referer": "https://y.qq.com/"},
                      "default_download_cookies": {"platform": "credential"}})
    cache_tracks([item])
    return item


@pytest.fixture
def client():
    return TestClient(app)


def issue(client, track):
    response = client.post(f"/api/v1/tracks/{track.id}/preview")
    assert response.status_code == 200, response.text
    return response.json()["url"]


def test_preview_requires_api_key(client, track, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret")
    path = f"/api/v1/tracks/{track.id}/preview"
    assert client.post(path).status_code == 401
    response = client.post(path, headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    assert "secret" not in response.json()["url"]
    assert response.json()["expires_at"] > time.time()


def test_unknown_or_unplayable_track(client, track):
    assert client.post("/api/v1/tracks/missing/preview").status_code == 404
    track.raw["download_url"] = "file:///etc/passwd"
    cache_tracks([track])
    assert client.post(f"/api/v1/tracks/{track.id}/preview").status_code == 400


def test_tampered_expired_or_evicted_preview(client, track, monkeypatch):
    from app import preview
    url = issue(client, track)
    assert client.get(url + "tampered").status_code == 401
    with monkeypatch.context() as clock:
        clock.setattr(preview.time, "time", lambda: 10**12)
        assert client.get(url).status_code == 401
    monkeypatch.setattr(preview, "get_cached", lambda _: None)
    assert client.get(url).status_code == 404


def mock_audio(monkeypatch, handler):
    from app import preview
    # Only the remote transport is replaced; HTTP requests and streaming stay real.
    monkeypatch.setattr(preview, "_new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_stream_forwards_range_and_platform_headers(client, track, monkeypatch):
    url = issue(client, track)
    def handler(request):
        assert request.headers["range"] == "bytes=4-7"
        assert request.headers["referer"] == "https://y.qq.com/"
        assert "platform=credential" in request.headers["cookie"]
        return httpx.Response(206, stream=httpx.ByteStream(b"4567"), headers={"Content-Length": "4", "Content-Type": "audio/mpeg", "Content-Range": "bytes 4-7/10", "Accept-Ranges": "bytes"})
    mock_audio(monkeypatch, handler)
    response = client.get(url, headers={"Range": "bytes=4-7"})
    assert response.status_code == 206
    assert response.content == b"4567"
    assert response.headers["content-range"] == "bytes 4-7/10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-length"] == "4"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status, content_type, expected", [(403, "text/html", 502), (200, "text/html", 502), (416, "audio/mpeg", 416)])
def test_upstream_errors(client, track, monkeypatch, status, content_type, expected):
    url = issue(client, track)
    mock_audio(monkeypatch, lambda _: httpx.Response(status, content=b"error", headers={"Content-Type": content_type, "Content-Range": "bytes */10"}))
    response = client.get(url)
    assert response.status_code == expected
    if expected == 416:
        assert response.headers["content-range"] == "bytes */10"


def test_upstream_timeout(client, track, monkeypatch):
    url = issue(client, track)
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)
    mock_audio(monkeypatch, handler)
    assert client.get(url).status_code == 504


def test_stream_closes_upstream(client, track, monkeypatch):
    url = issue(client, track)
    class AudioStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b"audio"
        async def aclose(self):
            self.closed = True
    stream = AudioStream()
    mock_audio(monkeypatch, lambda _: httpx.Response(200, stream=stream, headers={"Content-Type": "application/octet-stream"}))
    response = client.get(url)
    assert response.content == b"audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert stream.closed


@pytest.mark.parametrize("disconnect_during", ["http.response.start", "http.response.body"])
def test_disconnect_closes_upstream(track, monkeypatch, disconnect_during):
    import asyncio
    from app import preview
    from starlette.requests import ClientDisconnect

    class AudioStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b"audio"
        async def aclose(self):
            self.closed = True

    stream = AudioStream()
    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"Content-Type": "audio/mpeg"})))
    monkeypatch.setattr(preview, "_new_client", lambda: upstream_client)
    token = preview.issue(track.id)["url"].rsplit("/", 1)[1]
    async def exercise():
        response = await preview.stream(token)
        async def receive():
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == disconnect_during:
                raise OSError("client disconnected")
        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert upstream_client.is_closed
        assert stream.closed
    asyncio.run(exercise())


def test_disconnect_cancels_wait_for_upstream_headers(track, monkeypatch):
    import asyncio
    from app import preview
    from starlette.requests import ClientDisconnect, Request

    async def exercise():
        entered = asyncio.Event()
        canceled = False
        async def slow_source(request):
            nonlocal canceled
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                canceled = True
            return httpx.Response(200, content=b"audio")
        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(slow_source))
        monkeypatch.setattr(preview, "_new_client", lambda: upstream_client)
        async def receive():
            await entered.wait()
            return {"type": "http.disconnect"}
        request = Request({"type": "http"}, receive)
        token = preview.issue(track.id)["url"].rsplit("/", 1)[1]
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(preview.stream(token, request=request), timeout=1)
        assert upstream_client.is_closed
        assert canceled
    asyncio.run(exercise())
