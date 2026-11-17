"""Desktop connector routes follow the controlled rollout policy."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

import pytest

from app import main as app_main


CONNECTOR_PATHS = {
    "/api/connector/pairings",
    "/api/connector/heartbeat",
    "/api/incoming-assessments",
    "/api/platform/connectors/stats",
}
PREVIEW_PREFIX = "/__go360_local_arbonne_preview_117"


def _paths(monkeypatch, *, enabled, env):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("GO360_CONNECTOR_RELEASE_ENV", raising=False)
    if enabled:
        monkeypatch.setenv("GO360_CONNECTOR_ENABLED", "1")
    else:
        monkeypatch.delenv("GO360_CONNECTOR_ENABLED", raising=False)
    monkeypatch.setenv("GO360_ARB_PRIVATE_PREVIEW_117", "0")
    app = app_main.create_app()
    return {getattr(route, "path", "") for route in app.routes}


@pytest.mark.parametrize("env", ["dev", "development", "local", "test"])
def test_connector_routes_mount_for_an_enabled_development_environment(monkeypatch, env):
    paths = _paths(monkeypatch, enabled=True, env=env)
    assert CONNECTOR_PATHS <= paths
    assert "/kb" in paths
    assert not any(path.startswith(PREVIEW_PREFIX) for path in paths)


@pytest.mark.parametrize(
    ("enabled", "env"),
    [
        (False, "development"),
        (False, "production"),
        (True, "production"),
        (True, "staging"),
        (True, ""),
    ],
)
def test_connector_routes_stay_unmounted_outside_the_gate(monkeypatch, enabled, env):
    paths = _paths(monkeypatch, enabled=enabled, env=env)
    assert CONNECTOR_PATHS.isdisjoint(paths)
    assert "/kb" in paths
    assert not any(path.startswith(PREVIEW_PREFIX) for path in paths)
