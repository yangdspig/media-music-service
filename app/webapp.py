"""Serve one SPA at / and at the fnOS gateway without buffering API streams."""
from html import escape
from pathlib import Path

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

FNOS_PREFIX = "/app/MUSICASSISTANT"


class FnosGatewayMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            path = scope["path"]
            if path == FNOS_PREFIX and scope["type"] == "http":
                query = scope.get("query_string", b"").decode("latin-1")
                target = FNOS_PREFIX + "/" + (f"?{query}" if query else "")
                await RedirectResponse(target)(scope, receive, send)
                return
            if path.startswith(FNOS_PREFIX + "/"):
                # Starlette routes relative to root_path while retaining the full
                # path for redirects. Do not wrap receive/send: audio stays streamed.
                scope = {**scope, "root_path": FNOS_PREFIX}
        await self.app(scope, receive, send)


class SPAStaticFiles(StaticFiles):
    """HTML5 navigation fallback, with the deployment base set per request."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._index_path = Path(self.directory).resolve() / "index.html"
        self._index = self._index_path.read_text(encoding="utf-8")

    def file_response(self, full_path, stat_result, scope, status_code=200):
        if Path(full_path).resolve() == self._index_path:
            base = escape(scope.get("root_path", "").rstrip("/") + "/", quote=True)
            html = self._index.replace('<base href="/">', f'<base href="{base}">', 1)
            return HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-cache"})
        return super().file_response(full_path, stat_result, scope, status_code)

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            # Missing APIs and assets must retain 404 semantics instead of returning
            # index.html as JavaScript, JSON or an image.
            navigation = "text/html" in Headers(scope=scope).get("accept", "")
            if (error.status_code == 404 and path != "api" and not path.startswith(("api/", "assets/"))
                    and (navigation or not Path(path).suffix)):
                return await super().get_response("index.html", scope)
            raise
