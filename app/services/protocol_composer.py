# app/services/protocol_composer.py

from __future__ import annotations

from typing import Any, Dict, List

from app.services.product_ranker import rank_products, deduplicate_products, limit_products


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

    foundation: List[Dict[str, Any]] = []
    targeted: List[Dict[str, Any]] = []
    optional: List[Dict[str, Any]] = []

    custom_products = [p for p in products if _is_source(p, "custom")]
    na_products = [p for p in products if _is_source(p, "natural_approaches")]
    vh_products = [p for p in products if _is_source(p, "vitalhealth")]
    other_products = [
        p for p in products
        if not _is_source(p, "custom")
        and not _is_source(p, "natural_approaches")
        and not _is_source(p, "vitalhealth")
    ]

    # Foundation:
    # Prefer primary-aligned Natural Approaches items first, then fill with other top NA items.
    na_primary = [p for p in na_products if p.get("is_primary")]
    na_secondary = [p for p in na_products if not p.get("is_primary")]

    for p in na_primary[:2]:
        foundation.append(_phase_product(p, phase="foundation", is_core=True))

    for p in na_secondary:
        if len(foundation) >= 4:
            break
        foundation.append(_phase_product(p, phase="foundation", is_core=(len(foundation) < 2)))

    # Targeted:
    # Remaining NA first, then strongest VitalHealth, then custom.
    used_names = {p["name"] for p in foundation}

    remaining_na = [p for p in na_products if p["name"] not in used_names]
    for p in remaining_na[:6]:
        targeted.append(_phase_product(p, phase="targeted", is_core=(len(targeted) < 2)))

    remaining_vh = [p for p in vh_products if p["name"] not in used_names]
    for p in remaining_vh[:5]:
        targeted.append(_phase_product(p, phase="targeted", is_core=False))

    for p in custom_products:
        if p["name"] not in used_names and all(x["name"] != p["name"] for x in targeted):
            targeted.append(_phase_product(p, phase="targeted", is_core=True))

    # Optional:
    # Remaining VitalHealth and any other overflow.
    used_names.update(p["name"] for p in targeted)

    remaining_optional = [
        p for p in (vh_products + other_products)
        if p["name"] not in used_names
    ]
    for p in remaining_optional[:8]:
        optional.append(_phase_product(p, phase="optional", is_core=False))

    foundation = limit_products(foundation, 4)
    targeted = limit_products(targeted, 8)
    optional = limit_products(optional, 8)

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

