"""Public knowledge base pages stay available without a signed-in account."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_kb
from app.api.deps import get_db


def _client():
    app = FastAPI()
    app.include_router(routes_kb.router)
    app.dependency_overrides[get_db] = lambda: iter(())
    return TestClient(app)


def test_knowledge_home_returns_the_public_page(monkeypatch):
    monkeypatch.setattr(
        routes_kb,
        "get_portal_data",
        lambda db: {"systems": [], "patterns": [], "report_types": []},
    )
    response = _client().get("/kb")
    assert response.status_code == 200
    assert "Go360 QRMA Knowledgebase" in response.text
    assert "What would you like to understand?" in response.text

    slashed = _client().get("/kb/")
    assert slashed.status_code == 200


def test_robots_and_sitemap_follow_the_public_host(monkeypatch):
    monkeypatch.setattr(routes_kb, "get_az_index", lambda db: [{"go360_id": "CV-001"}])
    http = _client()

    local_robots = http.get("/robots.txt")
    assert local_robots.status_code == 200
    assert "User-agent: *" in local_robots.text
    assert local_robots.headers["content-type"].startswith("text/plain")

    local_sitemap = http.get("/sitemap.xml")
    assert local_sitemap.status_code == 404

    for name in ("APP_ENV", "GO360_ENV", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.setenv(name, "")
    headers = {"host": "app.go360.io"}
    robots = http.get("/robots.txt", headers=headers)
    assert robots.status_code == 200
    assert "Allow: /kb/" in robots.text
    assert "Sitemap: https://app.go360.io/sitemap.xml" in robots.text

    sitemap = http.get("/sitemap.xml", headers=headers)
    assert sitemap.status_code == 200
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "https://app.go360.io/kb/CV-001" in sitemap.text
    assert "https://app.go360.io/kb/a-z" in sitemap.text
