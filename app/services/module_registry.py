"""Versioned module ownership and dependency metadata."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ModuleCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,31}$")]
ModuleOwner = Literal[
    "CORE", "CLINIC", "SCHED", "QRMA", "HERBAL", "FUNC", "NATURO", "DISP",
    "STOCK", "PORTAL", "BILL", "COMMS", "AI", "ANALYTICS", "INTEGRATIONS", "SUBS",
]


class ModuleDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    module: ModuleCode
    kind: Literal["required", "optional", "conditional"]
    condition: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None

    @model_validator(mode="after")
    def validate_condition(self) -> ModuleDependency:
        if (self.kind == "conditional") != (self.condition is not None):
            raise ValueError("Only conditional dependencies must declare a condition.")
        return self


class ModuleDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ModuleCode
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    owner: ModuleOwner
    kind: Literal["foundation", "optional", "shared"]
    dependencies: tuple[ModuleDependency, ...] = ()


class ModuleRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Annotated[int, Field(strict=True, ge=1)]
    modules: tuple[ModuleDefinition, ...]

    @model_validator(mode="after")
    def validate_dependencies(self) -> ModuleRegistry:
        by_code = {}
        for module in self.modules:
            if module.code in by_code:
                raise ValueError(f"Duplicate module code: {module.code}")
            by_code[module.code] = module
        for module in self.modules:
            seen = set()
            for dependency in module.dependencies:
                if dependency.module not in by_code:
                    raise ValueError(f"Unknown dependency: {module.code} -> {dependency.module}")
                if dependency.module == module.code:
                    raise ValueError(f"Self dependency: {module.code}")
                if dependency.module in seen:
                    raise ValueError(f"Duplicate dependency: {module.code} -> {dependency.module}")
                seen.add(dependency.module)

        visited = set()
        visiting = set()

        def visit(code: str) -> None:
            if code in visiting:
                raise ValueError(f"Required or conditional dependency cycle at {code}")
            if code in visited:
                return
            visiting.add(code)
            for dependency in by_code[code].dependencies:
                if dependency.kind != "optional":
                    visit(dependency.module)
            visiting.remove(code)
            visited.add(code)

        for code in by_code:
            visit(code)
        return self

    def register(self, module: ModuleDefinition) -> ModuleRegistry:
        """Return a validated next revision, preserving this published revision."""
        return ModuleRegistry(version=self.version + 1, modules=(*self.modules, module))


@lru_cache(maxsize=1)
def get_module_registry() -> ModuleRegistry:
    path = Path(__file__).resolve().parents[1] / "config" / "module_registry.json"
    return ModuleRegistry.model_validate_json(path.read_text(encoding="utf-8"))
