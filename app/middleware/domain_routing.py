from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse
import os


APP_DOMAIN = os.getenv("APP_DOMAIN", "app.go360.io")
MARKETING_DOMAIN = os.getenv("MARKETING_DOMAIN", "www.go360.io")
STAGING_DOMAIN = os.getenv("STAGING_DOMAIN", "staging.go360.io")


class DomainRoutingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        path = request.url.path

        # Allow local dev
        if host in {"127.0.0.1", "localhost"}:
            return await call_next(request)

        # Root domain -> www
        if host == "go360.io":
            return RedirectResponse(
                url=f"https://{MARKETING_DOMAIN}{path}",
                status_code=301
            )

        # Marketing domain rules
        if host == MARKETING_DOMAIN:
            allowed_paths = {"/", "/terms", "/privacy"}

            if path in allowed_paths or path.startswith("/static/"):
                return await call_next(request)

            # Redirect everything else to app domain
            target = f"https://{APP_DOMAIN}{path}"
            if request.url.query:
                target += f"?{request.url.query}"

            return RedirectResponse(url=target, status_code=302)

        return await call_next(request)