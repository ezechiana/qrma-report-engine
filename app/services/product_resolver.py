# app/services/product_resolver.py

from __future__ import annotations

from typing import Any, Dict, List, Set

from app.data.vitalhealth_map import VITALHEALTH_CATEGORY_MAP
from app.data.na_protocol_map import NA_PROTOCOL_MAP


SOURCE_LABELS = {
    "natural_approaches": "Natural Approaches",
    "vitalhealth": "VitalHealth",
    "custom": "Custom plan",
    "system": "System guidance",
}


NORMAL_SEVERITIES = {"normal", "", None}


def display_source_label(source: str | None) -> str:
    return SOURCE_LABELS.get((source or "").strip().lower(), (source or "Other").replace("_", " ").title())


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


def _collect_flagged_marker_names(section: Any, limit: int = 5) -> List[str]:
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


def resolve_vitalhealth_products(report: Any) -> List[Dict[str, Any]]:
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

        marker_names = _collect_flagged_marker_names(section, limit=5)
        mapped_products = VITALHEALTH_CATEGORY_MAP[section_name].get("products", [])

        for product_name in mapped_products:
            if product_name not in product_map:
                product_map[product_name] = {
                    "name": product_name,
                    "source": "vitalhealth",
                    "source_label": display_source_label("vitalhealth"),
                    "supporting_sections": set(),
                    "supporting_markers": set(),
                    "pattern_alignment": None,
                    "focus_area": None,
                    "is_primary": False,
                    "rationale_fragments": [],
                }

            product_map[product_name]["supporting_sections"].add(section_name)

            for marker in marker_names:
                product_map[product_name]["supporting_markers"].add(marker)

            if marker_names:
                frag = (
                    f"Suggested based on overlapping findings across {section_name}, "
                    f"with flagged markers including {', '.join(marker_names)}."
                )
            else:
                frag = f"Suggested based on flagged findings in {section_name}."

            if frag not in product_map[product_name]["rationale_fragments"]:
                product_map[product_name]["rationale_fragments"].append(frag)

    results: List[Dict[str, Any]] = []

    for product in product_map.values():
        supporting_sections = sorted(product["supporting_sections"])
        supporting_markers = sorted(product["supporting_markers"])

        if supporting_markers:
            rationale = (
                f"Suggested based on overlapping findings across {', '.join(supporting_sections)}, "
                f"with flagged markers including {', '.join(supporting_markers[:5])}."
            )
        else:
            rationale = f"Suggested based on overlapping findings across {', '.join(supporting_sections)}."

        results.append(
            {
                "name": product["name"],
                "source": "vitalhealth",
                "source_label": display_source_label("vitalhealth"),
                "supporting_sections": supporting_sections,
                "supporting_markers": supporting_markers,
                "pattern_alignment": None,
                "focus_area": None,
                "is_primary": False,
                "rationale": rationale,
            }
        )

    results.sort(key=lambda x: x["name"])
    return results


def resolve_na_products(report: Any) -> List[Dict[str, Any]]:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    product_map: Dict[str, Dict[str, Any]] = {}

    def add_pattern_products(pattern: Any, is_primary: bool = False) -> None:
        if not pattern:
            return

        config = NA_PROTOCOL_MAP.get(getattr(pattern, "key", None))
        if not config:
            return

        pattern_label = getattr(pattern, "label", "") or ""
        pattern_focuses = getattr(pattern, "suggested_focus_areas", None) or []

        for p in config.get("products", []):
            product_name = p.get("name")
            focus = p.get("focus", "") or ""

            if not product_name:
                continue

            if product_name not in product_map:
                product_map[product_name] = {
                    "name": product_name,
                    "source": "natural_approaches",
                    "source_label": display_source_label("natural_approaches"),
                    "supporting_sections": set(),
                    "supporting_markers": set(),
                    "pattern_alignment": pattern_label,
                    "focus_area": focus,
                    "is_primary": is_primary,
                }
            else:
                if is_primary:
                    product_map[product_name]["is_primary"] = True

            # preserve strongest pattern alignment if primary
            if is_primary and pattern_label:
                product_map[product_name]["pattern_alignment"] = pattern_label

            if focus:
                product_map[product_name]["focus_area"] = focus

            # Optional future use if you later want to map focus areas from pattern itself
            for area in pattern_focuses:
                if area and not product_map[product_name].get("focus_area"):
                    product_map[product_name]["focus_area"] = area

    if primary:
        add_pattern_products(primary, is_primary=True)

    for p in patterns[1:4]:
        add_pattern_products(p, is_primary=False)

    results: List[Dict[str, Any]] = []

    for product in product_map.values():
        pattern_alignment = product.get("pattern_alignment")
        focus_area = product.get("focus_area")

        if pattern_alignment and focus_area:
            rationale = (
                f"Aligned with {pattern_alignment.lower()} and intended to support "
                f"{focus_area.replace('_', ' ')}."
            )
        elif pattern_alignment:
            rationale = f"Aligned with {pattern_alignment.lower()}."
        elif focus_area:
            rationale = f"Intended to support {focus_area.replace('_', ' ')}."
        else:
            rationale = "Aligned with the dominant functional pattern."

        results.append(
            {
                "name": product["name"],
                "source": "natural_approaches",
                "source_label": display_source_label("natural_approaches"),
                "supporting_sections": [],
                "supporting_markers": [],
                "pattern_alignment": product.get("pattern_alignment"),
                "focus_area": product.get("focus_area"),
                "is_primary": product.get("is_primary", False),
                "rationale": rationale,
            }
        )

    results.sort(key=lambda x: x["name"])
    return results


def resolve_all_products(report: Any) -> List[Dict[str, Any]]:
    vitalhealth_products = resolve_vitalhealth_products(report)
    na_products = resolve_na_products(report)

    # Keep products distinct by name, but preserve metadata from whichever source produced them.
    # If the same product name ever appears from multiple sources later, merge carefully.
    product_map: Dict[str, Dict[str, Any]] = {}

    def add_product(product: Dict[str, Any]) -> None:
        key = product["name"]

        if key not in product_map:
            product_map[key] = {
                "name": key,
                "source": product.get("source"),
                "source_label": product.get("source_label"),
                "supporting_sections": list(product.get("supporting_sections", [])),
                "supporting_markers": list(product.get("supporting_markers", [])),
                "pattern_alignment": product.get("pattern_alignment"),
                "focus_area": product.get("focus_area"),
                "is_primary": product.get("is_primary", False),
                "rationale": product.get("rationale", ""),
            }
            return

        existing = product_map[key]

        # Preserve source label if missing
        if not existing.get("source"):
            existing["source"] = product.get("source")
        if not existing.get("source_label"):
            existing["source_label"] = product.get("source_label")

        # Merge sections / markers
        merged_sections: Set[str] = set(existing.get("supporting_sections", []))
        merged_sections.update(product.get("supporting_sections", []))
        existing["supporting_sections"] = sorted(merged_sections)

        merged_markers: Set[str] = set(existing.get("supporting_markers", []))
        merged_markers.update(product.get("supporting_markers", []))
        existing["supporting_markers"] = sorted(merged_markers)

        # Preserve primary alignment if available
        if product.get("is_primary"):
            existing["is_primary"] = True
        if not existing.get("pattern_alignment") and product.get("pattern_alignment"):
            existing["pattern_alignment"] = product.get("pattern_alignment")
        if not existing.get("focus_area") and product.get("focus_area"):
            existing["focus_area"] = product.get("focus_area")

        # Prefer longer rationale if current is empty / shorter
        if len(product.get("rationale", "")) > len(existing.get("rationale", "")):
            existing["rationale"] = product.get("rationale", "")

    for p in na_products:
        add_product(p)

    for p in vitalhealth_products:
        add_product(p)

    results = list(product_map.values())

    def sort_key(item: Dict[str, Any]):
        source = (item.get("source") or "").lower()
        if source == "natural_approaches":
            priority = 0
        elif source == "vitalhealth":
            priority = 1
        elif source == "custom":
            priority = 2
        else:
            priority = 3
        return (priority, item["name"])

    results.sort(key=sort_key)
    return results