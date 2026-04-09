# app/services/product_resolver.py

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from app.config.product_recommendation_settings import (
    DEFAULT_VITALHEALTH_STRATEGY,
    normalize_vitalhealth_strategy,
)
from app.config.product_stack_rules import (
    JUSTIFIED_FAMILY_EXPANSION_BY_FOCUS,
    get_family_limit,
    get_product_family,
)
from app.data.na_protocol_map import NA_PROTOCOL_MAP
from app.data.vitalhealth_catalog import (
    VITALHEALTH_CATALOG,
    flatten_link_names,
    normalize_vitalhealth_product_name,
)
from app.data.vitalhealth_mechanism_map import (
    merge_mechanism_sets,
    resolve_pattern_mechanisms,
    resolve_section_mechanisms,
)

try:
    from app.data.vitalhealth_map import VITALHEALTH_CATEGORY_MAP  # type: ignore
except Exception:
    VITALHEALTH_CATEGORY_MAP = {}


SOURCE_LABELS = {
    "natural_approaches": "Natural Approaches",
    "vitalhealth": "VitalHealth",
    "custom": "Custom plan",
    "system": "System guidance",
}

NORMAL_SEVERITIES = {"normal", "", None}

PRIORITY_TO_WEIGHT = {
    1: 3,
    2: 2,
    3: 1,
}


def display_source_label(source: str | None) -> str:
    return SOURCE_LABELS.get(
        (source or "").strip().lower(),
        (source or "Other").replace("_", " ").title(),
    )


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


def _collect_flagged_sections(report: Any) -> List[Dict[str, Any]]:
    sections = getattr(report, "sections", []) or []
    flagged: List[Dict[str, Any]] = []

    for section in sections:
        title = _get_section_title(section)
        if not title:
            continue
        if not _has_flagged_markers(section):
            continue

        flagged.append(
            {
                "title": title,
                "markers": _collect_flagged_marker_names(section, limit=5),
            }
        )

    return flagged


def _pattern_keys_for_products(report: Any) -> List[str]:
    keys: List[str] = []
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    if primary and getattr(primary, "key", None):
        keys.append(primary.key)

    for p in patterns[1:4]:
        key = getattr(p, "key", None)
        if key:
            keys.append(key)

    seen: Set[str] = set()
    out: List[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _accumulate_weight_map(links: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in links or []:
        name = (item.get("name") or "").strip()
        weight = int(item.get("weight", 0) or 0)
        if not name or weight <= 0:
            continue
        out[name] = out.get(name, 0) + weight
    return out


def _priority_weight(priority: int | None) -> int:
    if priority is None:
        return 1
    return PRIORITY_TO_WEIGHT.get(priority, 1)


def _link_priority_map(links: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in links or []:
        name = (item.get("name") or "").strip()
        priority = int(item.get("priority", 99) or 99)
        if not name:
            continue
        if name not in out or priority < out[name]:
            out[name] = priority
    return out


def _score_overlap(inferred_weights: Dict[str, int], product_links: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    if not inferred_weights or not product_links:
        return 0, []

    score = 0
    matched: List[str] = []
    product_priority_map = _link_priority_map(product_links)

    for name, inferred_weight in inferred_weights.items():
        if name not in product_priority_map:
            continue
        matched.append(name)
        score += inferred_weight * _priority_weight(product_priority_map[name])

    return score, matched


def _collect_direct_vitalhealth_hits(flagged_sections: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    product_map: Dict[str, Dict[str, Any]] = {}

    for section in flagged_sections:
        section_name = section["title"]
        marker_names = section["markers"]

        mapping = VITALHEALTH_CATEGORY_MAP.get(section_name, {})
        mapped_products = mapping.get("products", [])

        for raw_name in mapped_products:
            product_name = normalize_vitalhealth_product_name(raw_name)

            if product_name not in product_map:
                product_map[product_name] = {
                    "supporting_sections": set(),
                    "supporting_markers": set(),
                    "direct_hits": 0,
                }

            product_map[product_name]["supporting_sections"].add(section_name)
            product_map[product_name]["direct_hits"] += 1

            for marker in marker_names:
                product_map[product_name]["supporting_markers"].add(marker)

    return product_map


def _build_supporting_markers_fallback(flagged_sections: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    marker_pool: List[str] = []
    for s in flagged_sections:
        for marker in s["markers"]:
            if marker not in marker_pool:
                marker_pool.append(marker)
            if len(marker_pool) >= limit:
                return marker_pool
    return marker_pool


def _legacy_result_from_direct_hit(
    product_name: str,
    direct_hit: Dict[str, Any],
) -> Dict[str, Any]:
    supporting_sections = sorted(direct_hit["supporting_sections"])
    supporting_markers = sorted(direct_hit["supporting_markers"])

    if supporting_markers:
        rationale = (
            f"Suggested based on overlapping findings across {', '.join(supporting_sections)}, "
            f"with flagged markers including {', '.join(supporting_markers[:5])}."
        )
    else:
        rationale = f"Suggested based on overlapping findings across {', '.join(supporting_sections)}."

    return {
        "name": product_name,
        "source": "vitalhealth",
        "source_label": display_source_label("vitalhealth"),
        "supporting_sections": supporting_sections,
        "supporting_markers": supporting_markers[:5],
        "pattern_alignment": None,
        "focus_area": None,
        "is_primary": False,
        "rationale": rationale,
        "_score": 100 + (8 * int(direct_hit.get("direct_hits", 1))),
    }


def _apply_vitalhealth_stack_rules(
    products: List[Dict[str, Any]],
    strategy: str,
) -> List[Dict[str, Any]]:
    """
    Reduce obvious overlap within product families while still allowing a second
    item where the focus_area clearly justifies a differentiated use-case.
    """
    kept: List[Dict[str, Any]] = []
    family_counts: Dict[str, int] = {}
    family_focuses: Dict[str, Set[str]] = {}

    for product in products:
        family = get_product_family(product.get("name", ""))
        if not family:
            kept.append(product)
            continue

        limit = get_family_limit(strategy, family)
        current_count = family_counts.get(family, 0)

        focus = (product.get("focus_area") or "").strip().lower()
        used_focuses = family_focuses.setdefault(family, set())

        if current_count < limit:
            kept.append(product)
            family_counts[family] = current_count + 1
            if focus:
                used_focuses.add(focus)
            continue

        justified_focuses = {
            x.strip().lower()
            for x in JUSTIFIED_FAMILY_EXPANSION_BY_FOCUS.get(family, [])
            if x.strip()
        }

        if focus and focus in justified_focuses and focus not in used_focuses:
            kept.append(product)
            family_counts[family] = current_count + 1
            used_focuses.add(focus)
            continue

        # Otherwise skip the overlapping product
        continue

    return kept


def resolve_vitalhealth_products_legacy(report: Any) -> List[Dict[str, Any]]:
    flagged_sections = _collect_flagged_sections(report)
    direct_hits = _collect_direct_vitalhealth_hits(flagged_sections)

    results: List[Dict[str, Any]] = []
    for product_name, direct_hit in direct_hits.items():
        results.append(_legacy_result_from_direct_hit(product_name, direct_hit))

    results.sort(key=lambda x: (-x["_score"], x["name"]))
    for item in results:
        item.pop("_score", None)
    return results


def resolve_vitalhealth_products_mechanism(report: Any) -> List[Dict[str, Any]]:
    flagged_sections = _collect_flagged_sections(report)
    flagged_section_names = [s["title"] for s in flagged_sections]
    pattern_keys = _pattern_keys_for_products(report)

    section_resolution = resolve_section_mechanisms(flagged_section_names)
    pattern_resolution = resolve_pattern_mechanisms(pattern_keys)
    combined_resolution = merge_mechanism_sets(section_resolution, pattern_resolution)

    inferred_mechanism_weights = _accumulate_weight_map(combined_resolution.get("mechanisms", []))
    inferred_category_weights = _accumulate_weight_map(combined_resolution.get("categories", []))

    results: List[Dict[str, Any]] = []

    for raw_name, product in VITALHEALTH_CATALOG.items():
        product_name = normalize_vitalhealth_product_name(raw_name)

        mechanism_links = product.get("mechanisms", [])
        category_links = product.get("categories", [])
        system_links = product.get("systems", [])

        mechanism_score, matched_mechanisms = _score_overlap(inferred_mechanism_weights, mechanism_links)
        category_score, matched_categories = _score_overlap(inferred_category_weights, category_links)

        system_names = {x.lower() for x in flatten_link_names(system_links)}
        section_title_names = {x.lower() for x in flagged_section_names}
        system_score = 0
        if system_names & section_title_names:
            system_score = 2 * len(system_names & section_title_names)

        total_score = mechanism_score + category_score + system_score

        if total_score <= 0:
            continue

        supporting_sections = flagged_section_names[:4]
        supporting_markers = _build_supporting_markers_fallback(flagged_sections, limit=5)

        rationale_parts: List[str] = []

        if supporting_sections:
            rationale_parts.append(
                f"Suggested because related findings appear across {', '.join(supporting_sections[:4])}"
            )

        if matched_mechanisms:
            pretty_mechs = ", ".join(matched_mechanisms[:3]).replace("_", " ")
            rationale_parts.append(f"with strongest alignment to {pretty_mechs}")

        if not rationale_parts:
            rationale = "Suggested from the scan pattern and section evidence."
        else:
            rationale = " ".join(rationale_parts).strip().rstrip(".") + "."

        results.append(
            {
                "name": product_name,
                "source": "vitalhealth",
                "source_label": display_source_label("vitalhealth"),
                "supporting_sections": supporting_sections,
                "supporting_markers": supporting_markers,
                "pattern_alignment": ", ".join(matched_categories[:2]).replace("_", " ") if matched_categories else None,
                "focus_area": matched_mechanisms[0] if matched_mechanisms else None,
                "is_primary": False,
                "rationale": rationale,
                "_score": total_score,
            }
        )

    results.sort(key=lambda x: (-x["_score"], x["name"]))
    for item in results:
        item.pop("_score", None)
    return results


def resolve_vitalhealth_products_hybrid(report: Any) -> List[Dict[str, Any]]:
    legacy_products = resolve_vitalhealth_products_legacy(report)
    mechanism_products = resolve_vitalhealth_products_mechanism(report)

    product_map: Dict[str, Dict[str, Any]] = {}

    def add_product(product: Dict[str, Any], source_mode: str) -> None:
        key = normalize_vitalhealth_product_name(product["name"])

        if key not in product_map:
            product_map[key] = {
                "name": key,
                "source": "vitalhealth",
                "source_label": display_source_label("vitalhealth"),
                "supporting_sections": list(product.get("supporting_sections", [])),
                "supporting_markers": list(product.get("supporting_markers", [])),
                "pattern_alignment": product.get("pattern_alignment"),
                "focus_area": product.get("focus_area"),
                "is_primary": False,
                "rationale": product.get("rationale", ""),
                "_score": 0,
                "_legacy_hit": False,
                "_mechanism_hit": False,
            }

        existing = product_map[key]

        merged_sections: Set[str] = set(existing.get("supporting_sections", []))
        merged_sections.update(product.get("supporting_sections", []))
        existing["supporting_sections"] = sorted(merged_sections)

        merged_markers: Set[str] = set(existing.get("supporting_markers", []))
        merged_markers.update(product.get("supporting_markers", []))
        existing["supporting_markers"] = sorted(merged_markers)

        if not existing.get("pattern_alignment") and product.get("pattern_alignment"):
            existing["pattern_alignment"] = product.get("pattern_alignment")
        if not existing.get("focus_area") and product.get("focus_area"):
            existing["focus_area"] = product.get("focus_area")

        if source_mode == "legacy":
            existing["_legacy_hit"] = True
            existing["_score"] += 100
        elif source_mode == "mechanism":
            existing["_mechanism_hit"] = True
            existing["_score"] += 25

        if len(product.get("rationale", "")) > len(existing.get("rationale", "")):
            existing["rationale"] = product.get("rationale", "")

    for p in legacy_products:
        add_product(p, "legacy")

    for p in mechanism_products:
        add_product(p, "mechanism")

    results = list(product_map.values())

    for item in results:
        if item["_legacy_hit"] and item["_mechanism_hit"]:
            item["_score"] += 20
        elif item["_legacy_hit"]:
            item["_score"] += 10

        sections = item.get("supporting_sections", [])[:4]
        markers = item.get("supporting_markers", [])[:5]
        focus = item.get("focus_area")

        if item["_legacy_hit"] and item["_mechanism_hit"]:
            rationale = "Suggested from both the legacy VitalHealth mapping and the weighted mechanism model"
            if sections:
                rationale += f", with related findings across {', '.join(sections)}"
            if focus:
                rationale += f" and strongest alignment to {str(focus).replace('_', ' ')}"
            item["rationale"] = rationale.rstrip(".") + "."
        elif item["_legacy_hit"]:
            if markers:
                item["rationale"] = (
                    f"Suggested based on overlapping findings across {', '.join(sections)}, "
                    f"with flagged markers including {', '.join(markers)}."
                )
            else:
                item["rationale"] = f"Suggested based on overlapping findings across {', '.join(sections)}."
        else:
            if sections and focus:
                item["rationale"] = (
                    f"Suggested because related findings appear across {', '.join(sections)}, "
                    f"with strongest alignment to {str(focus).replace('_', ' ')}."
                )
            elif focus:
                item["rationale"] = (
                    f"Suggested from the weighted mechanism model, with strongest alignment to "
                    f"{str(focus).replace('_', ' ')}."
                )

    results.sort(key=lambda x: (-x["_score"], x["name"]))

    for item in results:
        item.pop("_score", None)
        item.pop("_legacy_hit", None)
        item.pop("_mechanism_hit", None)

    return results


def resolve_vitalhealth_products(report: Any, strategy: str | None = None) -> List[Dict[str, Any]]:
    mode = normalize_vitalhealth_strategy(strategy)

    if mode == "legacy_vitalhealth":
        results = resolve_vitalhealth_products_legacy(report)
    elif mode == "mechanism_weighted":
        results = resolve_vitalhealth_products_mechanism(report)
    else:
        results = resolve_vitalhealth_products_hybrid(report)

    return _apply_vitalhealth_stack_rules(results, strategy=mode)


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

            if is_primary and pattern_label:
                product_map[product_name]["pattern_alignment"] = pattern_label

            if focus:
                product_map[product_name]["focus_area"] = focus

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
                "source_label": product.get("source_label") or display_source_label("natural_approaches"),
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


def resolve_all_products(
    report: Any,
    vitalhealth_strategy: str | None = None,
) -> List[Dict[str, Any]]:
    strategy = normalize_vitalhealth_strategy(vitalhealth_strategy or DEFAULT_VITALHEALTH_STRATEGY)

    vitalhealth_products = resolve_vitalhealth_products(report, strategy=strategy)
    na_products = resolve_na_products(report)

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

        if not existing.get("source"):
            existing["source"] = product.get("source")
        if not existing.get("source_label"):
            existing["source_label"] = product.get("source_label")

        merged_sections: Set[str] = set(existing.get("supporting_sections", []))
        merged_sections.update(product.get("supporting_sections", []))
        existing["supporting_sections"] = sorted(merged_sections)

        merged_markers: Set[str] = set(existing.get("supporting_markers", []))
        merged_markers.update(product.get("supporting_markers", []))
        existing["supporting_markers"] = sorted(merged_markers)

        if product.get("is_primary"):
            existing["is_primary"] = True
        if not existing.get("pattern_alignment") and product.get("pattern_alignment"):
            existing["pattern_alignment"] = product.get("pattern_alignment")
        if not existing.get("focus_area") and product.get("focus_area"):
            existing["focus_area"] = product.get("focus_area")

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
