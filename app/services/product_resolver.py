# app/services/product_resolver.py

from __future__ import annotations

from typing import Any, Dict, List

from app.data.vitalhealth_map import VITALHEALTH_CATEGORY_MAP
from app.data.na_protocol_map import NA_PROTOCOL_MAP


NORMAL_SEVERITIES = {"normal", "", None}


def _get_section_title(section: Any) -> str:
    return (
        getattr(section, "display_title", None)
        or getattr(section, "source_title", None)
        or ""
    ).strip()


def _has_flagged_markers(section: Any) -> bool:
    params = getattr(section, "parameters", []) or []
    for p in params:
        sev = getattr(p, "severity", None)
        if sev not in NORMAL_SEVERITIES:
            return True
    return False

def rank_vitalhealth_products(products):
    priority_keywords = [
        "Gastrointestinal",
        "Amino Acid",
        "Vitamin",
        "Heavy Metal",
        "Cardiovascular",
    ]

    def score(p):
        rationale = p.get("rationale", "")
        return sum(1 for kw in priority_keywords if kw in rationale)

    products_sorted = sorted(products, key=score, reverse=True)

    core = products_sorted[:5]
    secondary = products_sorted[5:12]
    optional = products_sorted[12:]

    return {
        "core": core,
        "secondary": secondary,
        "optional": optional,
    }



def _collect_flagged_marker_names(section: Any, limit: int = 3) -> List[str]:
    names: List[str] = []
    params = getattr(section, "parameters", []) or []

    for p in params:
        sev = getattr(p, "severity", None)
        if sev in NORMAL_SEVERITIES:
            continue

        label = (
            getattr(p, "display_label", None)
            or getattr(p, "clinical_label", None)
            or getattr(p, "source_name", None)
            or ""
        ).strip()

        if label and label not in names:
            names.append(label)

        if len(names) >= limit:
            break

    return names


def resolve_vitalhealth_products(report: Any) -> List[Dict[str, str]]:
    sections = getattr(report, "sections", []) or []
    product_map: Dict[str, Dict[str, Any]] = {}

    for section in sections:
        section_name = _get_section_title(section)
        if not section_name:
            continue

        if section_name not in VITALHEALTH_CATEGORY_MAP:
            continue

        if not _has_flagged_markers(section):
            continue

        marker_names = _collect_flagged_marker_names(section, limit=3)
        mapped_products = VITALHEALTH_CATEGORY_MAP[section_name].get("products", [])

        for product_name in mapped_products:
            if product_name not in product_map:
                product_map[product_name] = {
                    "name": product_name,
                    "source": "vitalhealth",
                    "sections": set(),
                    "markers": set(),
                    "rationales": [],
                }

            product_map[product_name]["sections"].add(section_name)
            for m in marker_names:
                product_map[product_name]["markers"].add(m)

            if marker_names:
                rationale = (
                    f"Suggested for {section_name} based on flagged findings including "
                    f"{', '.join(marker_names)}."
                )
            else:
                rationale = f"Suggested for {section_name} based on flagged findings."

            product_map[product_name]["rationales"].append(rationale)

    results: List[Dict[str, str]] = []

    for product in product_map.values():
        section_list = sorted(product["sections"])
        marker_list = sorted(product["markers"])

        if marker_list:
            merged_rationale = (
                f"Suggested based on overlapping findings across {', '.join(section_list)}, "
                f"with flagged markers including {', '.join(marker_list[:5])}."
            )
        else:
            merged_rationale = (
                f"Suggested based on overlapping findings across {', '.join(section_list)}."
            )

        results.append(
            {
                "name": product["name"],
                "source": "vitalhealth",
                "rationale": merged_rationale,
            }
        )

    results.sort(key=lambda x: x["name"])
    return results


def resolve_na_products(report: Any) -> List[Dict[str, str]]:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    product_map: Dict[str, Dict[str, Any]] = {}

    def add_pattern_products(pattern: Any) -> None:
        if not pattern:
            return

        config = NA_PROTOCOL_MAP.get(getattr(pattern, "key", None))
        if not config:
            return

        for p in config.get("products", []):
            product_name = p.get("name")
            focus = p.get("focus", "")

            if not product_name:
                continue

            if product_name not in product_map:
                product_map[product_name] = {
                    "name": product_name,
                    "source": "natural_approaches",
                    "patterns": set(),
                    "focus_areas": set(),
                }

            product_map[product_name]["patterns"].add(getattr(pattern, "label", ""))
            if focus:
                product_map[product_name]["focus_areas"].add(focus)

    if primary:
        add_pattern_products(primary)

    for p in patterns[1:4]:
        add_pattern_products(p)

    results: List[Dict[str, str]] = []

    for product in product_map.values():
        pattern_list = [x for x in sorted(product["patterns"]) if x]
        focus_list = [x for x in sorted(product["focus_areas"]) if x]

        if pattern_list and focus_list:
            rationale = (
                f"Aligned with {', '.join(pattern_list).lower()} and intended to support "
                f"{', '.join(focus_list)}."
            )
        elif pattern_list:
            rationale = f"Aligned with {', '.join(pattern_list).lower()}."
        elif focus_list:
            rationale = f"Intended to support {', '.join(focus_list)}."
        else:
            rationale = "Aligned with the dominant functional pattern."

        results.append(
            {
                "name": product["name"],
                "source": "natural_approaches",
                "rationale": rationale,
            }
        )

    results.sort(key=lambda x: x["name"])
    return results


def resolve_all_products(report: Any) -> List[Dict[str, str]]:
    vitalhealth_products = resolve_vitalhealth_products(report)
    na_products = resolve_na_products(report)

    product_map: Dict[str, Dict[str, Any]] = {}

    def add_product(product: Dict[str, str]) -> None:
        key = product["name"]

        if key not in product_map:
            product_map[key] = {
                "name": key,
                "sources": set(),
                "rationales": [],
            }

        source = product.get("source", "").strip()
        rationale = product.get("rationale", "").strip()

        if source:
            product_map[key]["sources"].add(source)
        if rationale and rationale not in product_map[key]["rationales"]:
            product_map[key]["rationales"].append(rationale)

    for p in vitalhealth_products:
        add_product(p)

    for p in na_products:
        add_product(p)

    results: List[Dict[str, str]] = []

    for product in product_map.values():
        results.append(
            {
                "name": product["name"],
                "source": ", ".join(sorted(product["sources"])),
                "rationale": " | ".join(product["rationales"]),
            }
        )

    # Natural Approaches first, then VitalHealth, then alphabetical fallback
    def sort_key(item: Dict[str, str]):
        source = item.get("source", "")
        if "natural_approaches" in source and "vitalhealth" not in source:
            priority = 0
        elif "natural_approaches" in source and "vitalhealth" in source:
            priority = 1
        elif "vitalhealth" in source:
            priority = 2
        else:
            priority = 3
        return (priority, item["name"])

    results.sort(key=sort_key)
    return results