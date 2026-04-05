from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CATALOG_PATHS = {
    "natural_approaches": ROOT / "app" / "config" / "product_catalog_natural_approaches.json",
    "vital_health": ROOT / "app" / "config" / "product_catalog_vitalhealth_global.json",
    "practitioner_custom": ROOT / "app" / "config" / "practitioner_product_catalog.json",
}

RULE_PATHS = {
    "natural_approaches": ROOT / "app" / "config" / "product_rules_natural_approaches.json",
    "vital_health": ROOT / "app" / "config" / "product_rules_vitalhealth.json",
    "practitioner_custom": ROOT / "app" / "config" / "practitioner_product_rules.json",
}


def _load_json_safe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_product_profile(profile_name: str) -> tuple[dict, dict]:
    profile = (profile_name or "natural_approaches").strip().lower()
    catalog_path = CATALOG_PATHS.get(profile, CATALOG_PATHS["natural_approaches"])
    rules_path = RULE_PATHS.get(profile, RULE_PATHS["natural_approaches"])

    catalog = _load_json_safe(catalog_path)
    rules = _load_json_safe(rules_path)

    if not catalog:
        catalog = _load_json_safe(CATALOG_PATHS["natural_approaches"])
    if not rules:
        rules = _load_json_safe(RULE_PATHS["natural_approaches"])

    return catalog or {}, rules or {}
