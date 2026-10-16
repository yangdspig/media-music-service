"""Cached-track previews: scoped playback credentials and a Range-preserving relay."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response, StreamingResponse

from .config import settings
from .search import get_cached

_TOKEN_KEY = secrets.token_bytes(32)
_TOKEN_TTL = 3600
_AUDIO_TYPES = {"mp3": "audio/mpeg", "flac": "audio/flac", "m4a": "audio/mp4",
                "mp4": "audio/mp4", "aac": "audio/aac", "wav": "audio/wav",
                "ogg": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm"}


class _AudioResponse(StreamingResponse):
    def __init__(self, *args, close, **kwargs):
        super().__init__(*args, **kwargs)
        self._close = close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.body_iterator.aclose()
            finally:
                await self._close()


def _signature(payload: str) -> str:
    # Rotating the API key also invalidates previously issued playback URLs.
    message = f"{settings.api_key or ''}\0{payload}".encode()
    return hmac.new(_TOKEN_KEY, message, hashlib.sha256).hexdigest()


def _track(track_id: str):
    track = get_cached(track_id)
    if track is None:
        raise HTTPException(404, "曲目信息已过期，请重新解析榜单后试听")
    url = track.raw.get("download_url")
    parts = urlsplit(url) if isinstance(url, str) else None
    if (not parts or parts.scheme not in {"http", "https"} or not parts.hostname
            or str(track.raw.get("protocol") or "HTTP").upper() != "HTTP"):
        raise HTTPException(400, "当前曲目没有可直接试听的音频地址")
    return track


def issue(track_id: str) -> dict:
    _track(track_id)
    expires_at = int(time.time()) + _TOKEN_TTL
    payload = base64.urlsafe_b64encode(json.dumps([track_id, expires_at]).encode()).decode().rstrip("=")
    return {"url": f"/api/v1/preview/{payload}.{_signature(payload)}", "expires_at": expires_at}


def _verify(token: str) -> str:
    try:
        if len(token) > 4096:
            raise ValueError
        payload, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(payload)):
            raise ValueError
        track_id, expiry = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(track_id, str) or not isinstance(expiry, int) or expiry <= time.time():
            raise ValueError
        return track_id
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(401, "试听地址已失效，请重新点击歌曲播放") from None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, connect=10))


def _platform_headers(track) -> dict:
    headers = httpx.Headers({"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"})
    referers = {"QQMusicClient": "https://y.qq.com/", "NeteaseMusicClient": "https://music.163.com/"}
    if track.source in referers:
        headers["Referer"] = referers[track.source]
    headers.update(track.raw.get("default_download_headers") or {})
    # Never reuse a download's byte offset or transfer encoding for a preview.
    for name in ("range", "host", "content-length", "connection"):
        headers.pop(name, None)
    headers["Accept-Encoding"] = "identity"
    cfg = settings.sources.get(track.source)
    cookies = track.raw.get("default_download_cookies") or (cfg.download_cookies if cfg else None)
    if isinstance(cookies, dict):
        cookie = SimpleCookie()
        for key, value in cookies.items():
            cookie[key] = str(value)
        headers["Cookie"] = cookie.output(header="", sep=";").strip()
    elif isinstance(cookies, str):
        headers["Cookie"] = cookies
    return dict(headers)


async def _send_connected(client, upstream_request, request: Request | None):
    if request is None:
        return await client.send(upstream_request, stream=True)
    ready = anyio.Event()
    response, error, disconnected = None, None, False

    async def open_upstream():
        nonlocal response, error
        try:
            response = await client.send(upstream_request, stream=True)
        except Exception as exc:
            error = exc
        finally:
            ready.set()

    async def watch_disconnect():
        nonlocal disconnected
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                disconnected = True
                ready.set()
                return

    async with anyio.create_task_group() as group:
        group.start_soon(open_upstream)
        group.start_soon(watch_disconnect)
        await ready.wait()
        group.cancel_scope.cancel()
    if disconnected:
        if response is not None:
            await response.aclose()
        raise ClientDisconnect()
    if error is not None:
        raise error
    return response


async def stream(token: str, byte_range: str | None = None, request: Request | None = None) -> Response:
    track = _track(_verify(token))
    headers = _platform_headers(track)
    if byte_range:
        if not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range):
            raise HTTPException(416, "不支持的音频范围请求")
        headers["Range"] = byte_range
    client = _new_client()
    upstream = None

    async def close():
        with anyio.CancelScope(shield=True):
            try:
                if upstream is not None:
                    await upstream.aclose()
            finally:
                await client.aclose()

    try:
        upstream = await _send_connected(client, client.build_request("GET", track.raw["download_url"], headers=headers), request)
        if upstream.status_code == 416:
            response_headers = {"Cache-Control": "no-store"}
            if value := upstream.headers.get("content-range"):
                response_headers["Content-Range"] = value
            await close()
            return Response(status_code=416, headers=response_headers)
        if upstream.status_code not in {200, 206}:
            raise HTTPException(502, "音源暂时无法访问，请重新解析榜单后重试")
        content_type = upstream.headers.get("content-type", "").split(";")[0].lower()
        if content_type and not (content_type.startswith("audio/") or content_type in {"application/octet-stream", "binary/octet-stream", "video/mp4"}):
            raise HTTPException(502, "音源返回了无效音频，请重新解析榜单后重试")
        response_headers = {name: upstream.headers[name] for name in
                            ("content-length", "content-range", "accept-ranges", "content-encoding") if name in upstream.headers}
        response_headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        if not content_type.startswith("audio/"):
            content_type = _AUDIO_TYPES.get((track.ext or "").lower().lstrip("."), "application/octet-stream")

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await close()

        return _AudioResponse(body(), status_code=upstream.status_code, headers=response_headers,
                              media_type=content_type, close=close)
    except httpx.TimeoutException:
        await close()
        raise HTTPException(504, "音源响应超时，请稍后重试") from None
    except httpx.HTTPError:
        await close()
        raise HTTPException(502, "无法连接音源，请稍后重试") from None
    except BaseException:
        await close()
        raise
