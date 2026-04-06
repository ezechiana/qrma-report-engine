# app/services/protocol_composer.py

from __future__ import annotations

from typing import Any, Dict, List

from app.services.product_ranker import rank_products, deduplicate_products


CONFIG = {
    "include_natural_approaches": True,
    "include_vitalhealth": True,
    "include_custom": True,
    "include_system": True,
    # modes:
    # - "source_balanced": allow NA + VitalHealth to appear in Foundation / Targeted
    # - "na_first": prefer NA in Foundation, but do not remove VitalHealth from later phases
    "protocol_mode": "source_balanced",
}


def _is_source(product: Dict[str, Any], source_name: str) -> bool:
    return (product.get("source") or "").strip().lower() == source_name


def _phase_product(
    product: Dict[str, Any],
    *,
    phase: str,
    is_core: bool = False,
) -> Dict[str, Any]:
    item = dict(product)
    item["phase"] = phase
    item["is_core"] = is_core
    return item


def filter_products_by_source(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []

    for p in products:
        source = (p.get("source") or "").strip().lower()

        if source == "natural_approaches" and not CONFIG["include_natural_approaches"]:
            continue
        if source == "vitalhealth" and not CONFIG["include_vitalhealth"]:
            continue
        if source == "custom" and not CONFIG["include_custom"]:
            continue
        if source == "system" and not CONFIG["include_system"]:
            continue

        filtered.append(p)

    return filtered


def _append_unique_products(
    target: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    used_names: set[str],
    *,
    phase: str,
    core_limit: int = 0,
    max_count: int | None = None,
) -> None:
    added = 0

    for p in candidates:
        name = p.get("name")
        if not name or name in used_names:
            continue

        is_core = core_limit > 0 and added < core_limit
        target.append(_phase_product(p, phase=phase, is_core=is_core))
        used_names.add(name)
        added += 1

        if max_count is not None and added >= max_count:
            break


def compose_protocol(report: Any, products: List[Dict[str, Any]]) -> Dict[str, Any]:
    primary = getattr(report, "primary_pattern", None)

    if primary:
        title = f"{primary.label} Support Plan"
        summary = (
            "This plan focuses on addressing the primary functional pattern, "
            "starting with foundational support before moving into targeted optimisation."
        )
    else:
        title = "Personalised Support Plan"
        summary = (
            "This plan groups the most relevant support options into a staged structure "
            "to help guide discussion and follow-up."
        )

    products = deduplicate_products(products)
    products = rank_products(products)
    products = filter_products_by_source(products)

    foundation: List[Dict[str, Any]] = []
    targeted: List[Dict[str, Any]] = []
    optional: List[Dict[str, Any]] = []

    custom_products = [p for p in products if _is_source(p, "custom")]
    na_products = [p for p in products if _is_source(p, "natural_approaches")]
    vh_products = [p for p in products if _is_source(p, "vitalhealth")]
    system_products = [p for p in products if _is_source(p, "system")]
    other_products = [
        p for p in products
        if not _is_source(p, "custom")
        and not _is_source(p, "natural_approaches")
        and not _is_source(p, "vitalhealth")
        and not _is_source(p, "system")
    ]

    used_names: set[str] = set()
    mode = CONFIG.get("protocol_mode", "source_balanced")

    if mode == "na_first":
        # Foundation: mostly NA/custom, but still allow some VH if present
        _append_unique_products(
            foundation,
            custom_products + na_products,
            used_names,
            phase="foundation",
            core_limit=2,
            max_count=4,
        )
        _append_unique_products(
            foundation,
            vh_products,
            used_names,
            phase="foundation",
            core_limit=0,
            max_count=max(0, 5 - len(foundation)),
        )

        # Targeted: next wave from all enabled sources
        _append_unique_products(
            targeted,
            na_products + custom_products + vh_products + system_products + other_products,
            used_names,
            phase="targeted",
            core_limit=2,
            max_count=10,
        )

    else:
        # source_balanced
        # Foundation: explicitly allow both NA/custom and VitalHealth into the first phase
        _append_unique_products(
            foundation,
            custom_products + na_products,
            used_names,
            phase="foundation",
            core_limit=2,
            max_count=3,
        )
        _append_unique_products(
            foundation,
            vh_products,
            used_names,
            phase="foundation",
            core_limit=1 if len(foundation) < 2 else 0,
            max_count=max(0, 5 - len(foundation)),
        )

        # Targeted: continue with best remaining products from all enabled sources
        _append_unique_products(
            targeted,
            na_products + custom_products + vh_products + system_products + other_products,
            used_names,
            phase="targeted",
            core_limit=2,
            max_count=12,
        )

    # Optional: include every remaining enabled product so nothing is lost
    _append_unique_products(
        optional,
        na_products + vh_products + custom_products + system_products + other_products,
        used_names,
        phase="optional",
        core_limit=0,
        max_count=None,
    )

    phases = [
        {
            "key": "foundation",
            "title": "Phase 1 — Foundation",
            "products": foundation,
        },
        {
            "key": "targeted",
            "title": "Phase 2 — Targeted Support",
            "products": targeted,
        },
        {
            "key": "optional",
            "title": "Phase 3 — Optional / Optimisation",
            "products": optional,
        },
    ]

    phases = [phase for phase in phases if phase["products"]]

    return {
        "title": title,
        "summary": summary,
        "phases": phases,
    }