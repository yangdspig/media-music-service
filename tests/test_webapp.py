"""The same service must work at / and behind the fnOS gateway, including audio."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import Track
from app.search import cache_tracks


@pytest.mark.parametrize("base", ["", "/app/MUSICASSISTANT"])
def test_health_and_auth_work_at_both_entries(base, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "gateway-test-key")
    client = TestClient(app)
    response = client.get(f"{base}/api/v1/health")
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["ok"] is True
    assert client.get(f"{base}/api/v1/downloads").status_code == 401
    response = client.get(f"{base}/api/v1/downloads", headers={"X-API-Key": "gateway-test-key"})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_gateway_redirect_keeps_browser_origin():
    response = TestClient(app).get("/app/MUSICASSISTANT?source=desktop", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/MUSICASSISTANT/?source=desktop"


@pytest.mark.parametrize("base", ["", "/app/MUSICASSISTANT"])
def test_preview_url_and_range_stay_inside_entry(base, monkeypatch):
    from app import preview

    track = Track(id="QQMusicClient:gateway-preview", source="QQMusicClient", title="入口试听", ext="mp3",
                  raw={"download_url": "https://audio.example/song.mp3"})
    cache_tracks([track])
    client = TestClient(app)
    response = client.post(f"{base}/api/v1/tracks/{track.id}/preview")
    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith(f"{base}/api/v1/preview/")

    def audio(request):
        assert request.headers["range"] == "bytes=2-4"
        return httpx.Response(206, stream=httpx.ByteStream(b"234"), headers={
            "Content-Type": "audio/mpeg", "Content-Range": "bytes 2-4/10", "Content-Length": "3",
        })

    monkeypatch.setattr(preview, "_new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(audio)))
    response = client.get(url, headers={"Range": "bytes=2-4"})
    assert response.status_code == 206
    assert response.content == b"234"
    assert response.headers["content-range"] == "bytes 2-4/10"


@pytest.fixture
def static_client(tmp_path):
    from app.webapp import FnosGatewayMiddleware, SPAStaticFiles

    (tmp_path / "index.html").write_text('<html><head><base href="/"></head><body>music</body></html>')
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "chunk.js").write_text('export default "music"')
    web = FastAPI()
    web.add_middleware(FnosGatewayMiddleware)
    web.mount("/", SPAStaticFiles(directory=tmp_path, html=True))
    return TestClient(web)


@pytest.mark.parametrize("base", ["", "/app/MUSICASSISTANT"])
@pytest.mark.parametrize("page", ["/", "/login", "/albums/123", "/fnos/Favorites.2026"])
def test_html_base_supports_deep_link_reload(static_client, base, page):
    response = static_client.get(f"{base}{page}", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert f'<base href="{base}/">' in response.text
    assert response.headers["cache-control"] == "no-cache"
    asset = static_client.get(f"{base}/assets/chunk.js")
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.text == 'export default "music"'


@pytest.mark.parametrize("base", ["", "/app/MUSICASSISTANT"])
@pytest.mark.parametrize("path", ["/api/v1/missing", "/assets/missing.js", "/missing.svg"])
def test_missing_api_and_assets_are_not_spa_html(static_client, base, path):
    response = static_client.get(f"{base}{path}")
    assert response.status_code == 404
    assert "<html>" not in response.text
