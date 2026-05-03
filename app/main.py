from __future__ import annotations

import os
import urllib.parse
from contextlib import asynccontextmanager

from app.api import routes_ui
from app.api import routes_share
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager

from app.api.routes import router as engine_router
from app.api.routes_auth import router as auth_router
from app.api.routes_cases import router as cases_router
from app.api.routes_patients import router as patients_router
from app.api.routes_reports import router as reports_router
from app.api.routes_share import router as share_router
from app.api.routes_ui import router as ui_router
from app.api.routes_settings import router as settings_router
from app.api.routes_subscriptions import router as subscriptions_router
from app.db.base import Base
from app.db.session import engine
from app.api.routes_trend_reports import router as trend_reports_router
from app.api.routes_share_bundles import router as share_bundle_router
from app.api.routes_share_dashboard import router as share_dashboard_router
from app.api.routes_revenue import router as revenue_router
from app.api.routes_share_pages import router as share_pages_router
from app.db.migrate import run_migrations

APP_TITLE = os.getenv("APP_TITLE", "QRMA SaaS MVP")
APP_ENV = os.getenv("APP_ENV", "development")
AUTO_CREATE_TABLES = os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true"
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App startup/shutdown lifecycle.

    Railway-ready behaviour:
    - allows startup even if DB is temporarily unavailable during first boot
    - optionally auto-creates tables when AUTO_CREATE_TABLES=true
    """
    if AUTO_CREATE_TABLES:
        try:
            Base.metadata.create_all(bind=engine)
            print("✅ Database connected and tables ensured.")
        except Exception as exc:
            print(f"⚠️ Database startup check failed: {exc}")
            if APP_ENV != "production":
                raise
            # In production on Railway, allow app boot so deploy/debug is easier.
            # You can tighten this later once infra is stable.

    yield




def _parse_cors_origins(value: str) -> list[str]:
    value = (value or "").strip()
    if value == "*" or value == "":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]




def create_app() -> FastAPI:
    app = FastAPI(
        title=APP_TITLE,
        lifespan=lifespan,
    )

    cors_origins = _parse_cors_origins(CORS_ALLOW_ORIGINS)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=("*" not in cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )


    @app.middleware("http")
    async def browser_auth_redirect_middleware(request, call_next):
        """
        Convert hard browser navigation to protected /app pages from raw JSON
        401 into a normal login redirect with a next= return target.

        API/fetch calls still receive JSON 401, allowing the frontend to attempt
        silent refresh and then show the session-expired banner if refresh fails.
        """
        response = await call_next(request)

        path = request.url.path or ""
        accept = request.headers.get("accept", "")
        sec_fetch_mode = request.headers.get("sec-fetch-mode", "")

        is_page_navigation = (
            request.method.upper() == "GET"
            and path.startswith("/app")
            and response.status_code == 401
            and (
                "text/html" in accept
                or sec_fetch_mode == "navigate"
                or not path.startswith("/api")
            )
        )

        if is_page_navigation:
            next_target = path
            if request.url.query:
                next_target += "?" + request.url.query
            login_url = "/login?next=" + urllib.parse.quote(next_target, safe="") + "&session=expired"
            return RedirectResponse(url=login_url, status_code=303)

        return response


    # Auth
    app.include_router(auth_router)

    # SaaS application routes
    app.include_router(patients_router)
    app.include_router(cases_router)
    app.include_router(reports_router)
    app.include_router(share_router)
    app.include_router(settings_router)
    app.include_router(subscriptions_router)
    app.include_router(ui_router)
    app.include_router(routes_share.router)
    app.include_router(trend_reports_router)
    app.include_router(share_bundle_router)
    app.include_router(share_dashboard_router)
    app.include_router(revenue_router)
    app.include_router(share_pages_router)

    # Legacy engine/debug routes
    app.include_router(engine_router)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    @app.get("/api/status")
    def api_status():
        return {
            "status": "ok",
            "app": APP_TITLE,
            "environment": APP_ENV,
            "message": "QRMA SaaS API running",
        }


    @app.get("/health")
    def health():
        return {
            "status": "healthy",
            "environment": APP_ENV,
        }


    @app.on_event("startup")
    def startup():
        run_migrations(engine)

    return app




app = create_app()