from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, FileResponse
from pathlib import Path
import os
from app.services.search_visibility import prepare_search_state, apply_search_headers


APP_DOMAIN = os.getenv("APP_DOMAIN", "app.go360.io")
MARKETING_DOMAIN = os.getenv("MARKETING_DOMAIN", "www.go360.io")
STAGING_DOMAIN = os.getenv("STAGING_DOMAIN", "staging.go360.io")


class DomainRoutingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        prepare_search_state(request)
        response = await self._route(request, call_next)
        apply_search_headers(request, response)
        return response

    async def _route(self, request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        path = request.url.path

        # Same-origin browser fallback covers pages without explicit favicon markup.
        brand_aliases = {
            "/favicon.ico": ("favicon.ico", "image/x-icon"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
            "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
            "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
        }
        if request.method in {"GET", "HEAD"} and path in brand_aliases:
            filename, content_type = brand_aliases[path]
            file_path = Path(__file__).resolve().parents[1] / "static" / "brand" / "go360-v1" / filename
            return FileResponse(file_path, media_type=content_type, headers={"Cache-Control": "public, max-age=3600"})

        # Allow local dev
        if host in {"127.0.0.1", "localhost"}:
            return await call_next(request)

        # Keep the Clinical Reference publication URLs permanent.
        if host in {"go360.io", MARKETING_DOMAIN} and (path == "/kb" or path.startswith("/kb/")):
            target = f"https://{APP_DOMAIN}{path}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(url=target, status_code=308)

        # Root domain -> www
        if host == "go360.io":
            return RedirectResponse(
                url=f"https://{MARKETING_DOMAIN}{path}",
                status_code=301
            )

        # Marketing domain rules
        if host == MARKETING_DOMAIN:
            allowed_paths = {"/", "/terms", "/privacy", "/robots.txt", "/sitemap.xml"}

            if path in allowed_paths or path.startswith("/static/"):
                return await call_next(request)

            # Redirect everything else to app domain
            target = f"https://{APP_DOMAIN}{path}"
            if request.url.query:
                target += f"?{request.url.query}"

            return RedirectResponse(url=target, status_code=302)

        return await call_next(request)