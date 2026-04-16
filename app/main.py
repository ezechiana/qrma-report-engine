from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as engine_router
from app.api.routes_auth import router as auth_router
from app.api.routes_cases import router as cases_router
from app.api.routes_patients import router as patients_router
from app.api.routes_reports import router as reports_router
from app.api.routes_share import router as share_router
from app.db.base import Base
from app.db.session import engine


APP_TITLE = os.getenv("APP_TITLE", "QRMA SaaS MVP")
APP_ENV = os.getenv("APP_ENV", "development")
AUTO_CREATE_TABLES = os.getenv("AUTO_CREATE_TABLES", "true").lower() == "true"
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")


def _parse_cors_origins(value: str) -> list[str]:
    if value.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    App startup/shutdown lifecycle.

    For local MVP/dev we allow automatic table creation.
    In production, prefer Alembic migrations and set:
        AUTO_CREATE_TABLES=false
    """
    if AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=APP_TITLE,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(CORS_ALLOW_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth
    app.include_router(auth_router)

    # SaaS application routes
    app.include_router(patients_router)
    app.include_router(cases_router)
    app.include_router(reports_router)
    app.include_router(share_router)

    # Legacy engine/debug routes
    app.include_router(engine_router)

    @app.get("/")
    def root():
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

    return app


app = create_app()