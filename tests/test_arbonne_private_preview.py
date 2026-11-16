"""The Arbonne private preview stays unmounted unless the local gate is open."""

import asyncio
import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

import httpx
import pytest
from fastapi import FastAPI

from app import main as app_main
from app.services import go360_private_preview_117 as preview


PREVIEW_PREFIX = "/__go360_local_arbonne_preview_117"
REVIEW = PREVIEW_PREFIX + "/review"
TOKEN = "local-preview-token-24ch"


def _app(monkeypatch, *, enabled, env):
    if enabled:
        monkeypatch.setenv("GO360_ARB_PRIVATE_PREVIEW_117", "1")
    else:
        monkeypatch.delenv("GO360_ARB_PRIVATE_PREVIEW_117", raising=False)
    if env is None:
        monkeypatch.delenv("GO360_ENV", raising=False)
    else:
        monkeypatch.setenv("GO360_ENV", env)
    monkeypatch.delenv("GO360_CONNECTOR_ENABLED", raising=False)
    return app_main.create_app()


def _paths(monkeypatch, *, enabled, env):
    app = _app(monkeypatch, enabled=enabled, env=env)
    return {getattr(route, "path", "") for route in app.routes}, app


def _get(monkeypatch, path, *, env="test", flag="1", token=TOKEN, client_host="127.0.0.1", headers=None, params=None):
    monkeypatch.setenv("GO360_ENV", env)
    if flag is None:
        monkeypatch.delenv("GO360_ARB_PRIVATE_PREVIEW_117", raising=False)
    else:
        monkeypatch.setenv("GO360_ARB_PRIVATE_PREVIEW_117", flag)
    monkeypatch.setenv("GO360_ARB_PREVIEW_TOKEN", token)
    app = FastAPI()
    app.include_router(preview.router)
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))

    async def request():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, headers=headers, params=params)

    return asyncio.run(request())


def _pack(monkeypatch, tmp_path):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "input_family_registry.json").write_text(
        json.dumps(
            {
                "parents": [
                    {
                        "parent_id": "ARB-LOCAL",
                        "parent_name": "Local Preview Parent",
                        "family_label_candidate": "Local Family",
                        "uk_storefront_observations": [
                            {
                                "sku": "SKU-1",
                                "storefront_product_name": "Local Variant",
                                "variant": "Plain",
                                "market": "gb",
                                "language": "en",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (pack / "input_bundle_registry.json").write_text(
        json.dumps({"bundles": [{"id": "bundle-local"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(preview, "ROOT", pack)


@pytest.mark.parametrize("env", ["local", "development", "test", "Development"])
def test_preview_mounts_only_for_a_local_environment_with_the_flag(monkeypatch, env):
    paths, app = _paths(monkeypatch, enabled=True, env=env)
    assert any(path.startswith(PREVIEW_PREFIX) for path in paths)
    schema = app.openapi()
    assert PREVIEW_PREFIX not in schema["paths"]
    assert REVIEW not in schema["paths"]


@pytest.mark.parametrize(
    ("enabled", "env"),
    [
        (False, "development"),
        (False, "local"),
        (False, "test"),
        (True, "production"),
        (True, "staging"),
        (True, ""),
        (True, None),
        (False, "production"),
    ],
)
def test_preview_stays_unmounted_outside_the_gate(monkeypatch, enabled, env):
    paths, _app = _paths(monkeypatch, enabled=enabled, env=env)
    assert not any(path.startswith(PREVIEW_PREFIX) for path in paths)
    assert "/kb" in paths


def test_review_requires_the_flag_token_and_loopback(monkeypatch, tmp_path):
    _pack(monkeypatch, tmp_path)
    closed = _get(monkeypatch, REVIEW, flag="0", headers={"x-go360-preview-token": TOKEN})
    assert closed.status_code == 404

    missing_token = _get(monkeypatch, REVIEW)
    assert missing_token.status_code == 404

    short_secret = _get(
        monkeypatch,
        REVIEW,
        token="too-short",
        headers={"x-go360-preview-token": "too-short"},
    )
    assert short_secret.status_code == 404

    wrong_token = _get(monkeypatch, REVIEW, headers={"x-go360-preview-token": TOKEN + "-no"})
    assert wrong_token.status_code == 404

    forwarded = _get(
        monkeypatch,
        REVIEW,
        client_host="203.0.113.9",
        headers={"x-go360-preview-token": TOKEN, "x-forwarded-for": "127.0.0.1"},
    )
    assert forwarded.status_code == 404

    named_host = _get(
        monkeypatch,
        REVIEW,
        env="local",
        client_host="localhost",
        headers={"x-go360-preview-token": TOKEN},
    )
    assert named_host.status_code == 404

    test_peer = _get(
        monkeypatch,
        REVIEW,
        env="test",
        client_host="testclient",
        headers={"x-go360-preview-token": TOKEN},
    )
    assert test_peer.status_code == 200
    local_test_peer = _get(
        monkeypatch,
        REVIEW,
        env="local",
        client_host="testclient",
        headers={"x-go360-preview-token": TOKEN},
    )
    assert local_test_peer.status_code == 404

    production = _get(
        monkeypatch,
        REVIEW,
        env="production",
        client_host="127.0.0.1",
        headers={"x-go360-preview-token": TOKEN},
    )
    assert production.status_code == 404


def test_review_returns_an_unpublished_local_catalogue(monkeypatch, tmp_path):
    _pack(monkeypatch, tmp_path)
    response = _get(monkeypatch, REVIEW, headers={"x-go360-preview-token": TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "PRIVATE_LOCAL_ONLY"
    assert body["no_patient_data"] is True
    assert body["client_report_eligible"] == 0
    assert body["verified_bundle_memberships"] == 0
    assert body["bundle_candidates"] == 1
    assert body["products"][0]["publication_eligible"] is False
    assert body["products"][0]["family_approved"] is False
    assert body["products"][0]["variants"][0]["approved"] is False
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"

    unsupported = _get(
        monkeypatch,
        REVIEW,
        params={"market": "de", "language": "de"},
        headers={"x-go360-preview-token": TOKEN},
    )
    assert unsupported.status_code == 400


def test_review_stays_closed_when_the_local_pack_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(preview, "ROOT", tmp_path / "absent")
    response = _get(monkeypatch, REVIEW, headers={"x-go360-preview-token": TOKEN})
    assert response.status_code == 404
