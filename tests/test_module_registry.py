"""Module catalogue validation and its Platform Admin read entry point."""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import routes_platform_settings
from app.api.deps import get_current_user, get_db
from app.services.module_registry import ModuleDefinition, ModuleRegistry, get_module_registry


def definition(code="CORE", dependencies=()):
    return ModuleDefinition(
        code=code, name=code, owner="CORE", kind="foundation", dependencies=dependencies,
    )


def test_duplicate_registration_preserves_the_published_revision():
    registry = ModuleRegistry(version=1, modules=(definition(),))
    published = registry.model_dump_json()
    with pytest.raises(ValidationError, match="Duplicate module code"):
        registry.register(definition())
    assert registry.model_dump_json() == published
    next_revision = registry.register(definition("CLINIC"))
    assert next_revision.version == 2
    assert [module.code for module in next_revision.modules] == ["CORE", "CLINIC"]
    assert registry.model_dump_json() == published
    with pytest.raises(ValidationError):
        registry.modules[0].owner = "CLINIC"


@pytest.mark.parametrize(
    "modules, message",
    [
        ([{"code": "CORE", "dependencies": [{"module": "UNKNOWN", "kind": "required"}]}], "Unknown dependency"),
        ([{"code": "CORE", "dependencies": [{"module": "CORE", "kind": "optional"}]}], "Self dependency"),
        ([
            {"code": "CORE", "dependencies": [
                {"module": "CLINIC", "kind": "required"},
                {"module": "CLINIC", "kind": "optional"},
            ]},
            {"code": "CLINIC"},
        ], "Duplicate dependency"),
        ([
            {"code": "CORE", "dependencies": [{"module": "CLINIC", "kind": "required"}]},
            {"code": "CLINIC", "dependencies": [{"module": "CORE", "kind": "required"}]},
        ], "dependency cycle"),
        ([
            {"code": "CORE", "dependencies": [
                {"module": "CLINIC", "kind": "conditional", "condition": "test_required"},
            ]},
            {"code": "CLINIC", "dependencies": [{"module": "CORE", "kind": "required"}]},
        ], "dependency cycle"),
        ([{"code": "CORE", "dependencies": [{"module": "CLINIC", "kind": "conditional"}]}], "condition"),
        ([{"code": "CORE", "owner": "UNKNOWN"}], "owner"),
        ([{"code": "core"}], "pattern"),
    ],
)
def test_invalid_registry_is_rejected(modules, message):
    payload = [
        {"name": "Test module", "owner": "CORE", "kind": "optional", **module}
        for module in modules
    ]
    with pytest.raises(ValidationError, match=message):
        ModuleRegistry.model_validate({"version": 1, "modules": payload})


def test_blueprint_catalogue_has_foundations_and_conditional_stock():
    registry = get_module_registry()
    modules = {module.code: module for module in registry.modules}
    assert len(modules) == 16
    assert modules["CORE"].kind == modules["CLINIC"].kind == "foundation"
    assert modules["SUBS"].kind == "shared"
    assert all(module.owner == module.code for module in modules.values())
    stock = next(dependency for dependency in modules["DISP"].dependencies if dependency.module == "STOCK")
    assert stock.kind == "conditional"
    assert stock.condition == "managed_stock_required"
    # Optional integrations may refer to each other without a required cycle.
    ModuleRegistry(version=1, modules=(
        definition("CORE", [{"module": "CLINIC", "kind": "optional"}]),
        definition("CLINIC", [{"module": "CORE", "kind": "optional"}]),
    ))


def test_registry_api_requires_platform_admin(monkeypatch):
    app = FastAPI()
    app.include_router(routes_platform_settings.router)
    app.dependency_overrides[get_db] = lambda: None
    monkeypatch.setattr(
        "app.services.platform_settings_service.is_platform_admin_user",
        lambda db, user: user.role == "admin",
    )
    path = "/api/platform-settings/modules"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="practitioner")
        assert client.get(path).status_code == 403
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="admin")
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == get_module_registry().model_dump(mode="json")
