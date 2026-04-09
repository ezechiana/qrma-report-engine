# app/services/product_resolver.py

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from app.config.product_recommendation_settings import (
    DEFAULT_RECOMMENDATION_MODE,
    normalize_recommendation_mode,
    products_enabled,
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
from app.services.clinical_context import (
    contraindicates_blood_thinners,
    contraindicates_stimulants,
    context_focus_boosts,
    normalize_clinical_context,
)

try:
    from app.data.vitalhealth_map import VITALHEALTH_CATEGORY_MAP  # type: ignore
except Exception:
    VITALHEALTH_CATEGORY_MAP = {}


SOURCE_LABELS = {
    "natural_approaches": "Natural Approaches",
    "vitalhealth": "VitalHealth",
    "custom": "Custom plan",
    "custom_practitioner": "Practitioner Custom",
    "system": "System guidance",
}

NORMAL_SEVERITIES = {"normal", "", None}
PRIORITY_TO_WEIGHT = {1: 3, 2: 2, 3: 1}

# Broad, less-specific mechanism drivers that should not dominate
GENERIC_MECHANISM_KEYS = {
    "protein_nutrition",
    "antioxidant",
    "immune_modulation",
    "metabolic_balance_support",
}

# Broad focus labels that should be slightly penalised in clinically optimised mode
GENERIC_FOCUS_AREAS = {
    "protein_nutrition",
    "antioxidant",
    "immune_modulation",
    "metabolic_balance_support",
    "energy_stimulation",
}

# Stronger clinically meaningful focus areas
SPECIFIC_FOCUS_AREAS = {
    "digestive_support",
    "mucosal_support",
    "microbiome_support",
    "detox_phase_1",
    "detox_phase_2",
    "hepatic_support",
    "cognitive_support",
    "membrane_support",
    "male_vitality_support",
    "cardiovascular_support",
    "circulatory_support",
    "joint_cartilage_support",
    "connective_tissue_support",
}

CHILD_EXCLUDED_PRODUCTS = {
    "V-NRGY",
    "V-NRGY TROPICAL",
    "V-THERMOKAFE",
    "V-NEUROKAFE",
    "V-LOVKAFE",
    "PERFORMANCE+",
}

LIFESTYLE_STIMULANT_PRODUCTS = {
    "V-NRGY",
    "V-NRGY TROPICAL",
    "V-THERMOKAFE",
    "V-NEUROKAFE",
    "V-LOVKAFE",
    "PERFORMANCE+",
}

FOUNDATION_CLINICAL_PRODUCTS = {
    "D-FENZ",
    "GLUTATION PLUS+",
    "V-GLUTATION",
    "V-CURCUMAX",
    "V-OMEGA 3",
    "V-TE DETOX",
    "V-ORGANEX",
    "VITAL PRO",
    "VITALAGE COLLAGEN",
    "SMART BIOTICS",
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


def _is_child_report(report: Any) -> bool:
    profile = str(getattr(report, "report_profile", "") or "").lower()
    if "child" in profile:
        return True
    patient = getattr(report, "patient", None)
    age = getattr(patient, "age", None) if patient else None
    try:
        return age is not None and int(age) <= 16
    except Exception:
        return False


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


def _legacy_result_from_direct_hit(product_name: str, direct_hit: Dict[str, Any]) -> Dict[str, Any]:
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


def _apply_vitalhealth_stack_rules(products: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    if mode == "affiliate_vitalhealth":
        return products

    kept: List[Dict[str, Any]] = []
    family_counts: Dict[str, int] = {}
    family_focuses: Dict[str, Set[str]] = {}

    for product in products:
        family = get_product_family(product.get("name", ""))
        if not family:
            kept.append(product)
            continue

        if mode == "vitalhealth_clinical_optimised":
            stack_strategy = "mechanism_weighted"
        else:
            stack_strategy = "hybrid"

        limit = get_family_limit(stack_strategy, family)
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

    return kept


def _apply_context_filters(products: List[Dict[str, Any]], clinical_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not clinical_context:
        return products

    blocked: Set[str] = set()

    if contraindicates_stimulants(clinical_context):
        blocked.update(
            {
                "V-NRGY",
                "V-NRGY TROPICAL",
                "V-THERMOKAFE",
                "V-NEUROKAFE",
                "V-LOVKAFE",
            }
        )

    if contraindicates_blood_thinners(clinical_context):
        blocked.update(
            {
                "V-CURCUMAX",
                "V-OMEGA 3",
                "GLUTATION PLUS+",
                "V-GLUTATION",
            }
        )

    if not blocked:
        return products

    return [p for p in products if p.get("name") not in blocked]


def _apply_context_boosts(products: List[Dict[str, Any]], clinical_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not clinical_context:
        return products

    boosts = context_focus_boosts(clinical_context)
    if not boosts:
        return products

    rescored: List[Dict[str, Any]] = []
    for item in products:
        score = int(item.get("_score", 0) or 0)
        focus = (item.get("focus_area") or "").strip()
        if focus and focus in boosts:
            score += boosts[focus] * 5
        item = dict(item)
        item["_score"] = score
        rescored.append(item)

    return rescored


def _cap_vitalhealth_results(
    products: List[Dict[str, Any]],
    report: Any,
    mode: str,
) -> List[Dict[str, Any]]:
    if mode == "affiliate_vitalhealth":
        return products

    is_child = _is_child_report(report)

    if mode == "vitalhealth_clinical_optimised":
        max_total = 3 if is_child else 6
    elif mode == "mixed_clinical":
        max_total = 2 if is_child else 3
    else:
        return products

    if len(products) <= max_total:
        return products

    selected: List[Dict[str, Any]] = []
    used_names: Set[str] = set()
    used_families: Set[str] = set()

    # Pass 1: prefer diversity across families
    for product in products:
        if len(selected) >= max_total:
            break

        name = product.get("name", "")
        family = get_product_family(name)

        if name in used_names:
            continue

        if family and family in used_families:
            continue

        selected.append(product)
        used_names.add(name)
        if family:
            used_families.add(family)

    # Pass 2: fill remaining slots by remaining score order
    if len(selected) < max_total:
        for product in products:
            if len(selected) >= max_total:
                break
            name = product.get("name", "")
            if name in used_names:
                continue
            selected.append(product)
            used_names.add(name)

    return selected


def _mechanism_threshold(report: Any) -> int:
    return 22 if _is_child_report(report) else 20


def _product_mode_adjustment(
    product_name: str,
    report: Any,
    mode: str,
) -> int:
    """
    Extra scoring adjustment for clinical elegance.
    Does not affect strict affiliate mode.
    """
    if mode == "affiliate_vitalhealth":
        return 0

    is_child = _is_child_report(report)
    name = (product_name or "").strip()

    adjustment = 0

    if mode == "vitalhealth_clinical_optimised":
        if name in FOUNDATION_CLINICAL_PRODUCTS:
            adjustment += 2

        if name in LIFESTYLE_STIMULANT_PRODUCTS:
            adjustment -= 6

        if is_child and name in CHILD_EXCLUDED_PRODUCTS:
            adjustment -= 100

    elif mode == "mixed_clinical":
        if name in LIFESTYLE_STIMULANT_PRODUCTS:
            adjustment -= 8
        if is_child and name in CHILD_EXCLUDED_PRODUCTS:
            adjustment -= 100

    return adjustment


def resolve_vitalhealth_products_legacy(report: Any) -> List[Dict[str, Any]]:
    flagged_sections = _collect_flagged_sections(report)
    direct_hits = _collect_direct_vitalhealth_hits(flagged_sections)

    results: List[Dict[str, Any]] = []
    for product_name, direct_hit in direct_hits.items():
        results.append(_legacy_result_from_direct_hit(product_name, direct_hit))

    results.sort(key=lambda x: (-x["_score"], x["name"]))
    return results


def resolve_vitalhealth_products_mechanism(
    report: Any,
    clinical_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    flagged_sections = _collect_flagged_sections(report)
    flagged_section_names = [s["title"] for s in flagged_sections]
    pattern_keys = _pattern_keys_for_products(report)

    section_resolution = resolve_section_mechanisms(flagged_section_names)
    pattern_resolution = resolve_pattern_mechanisms(pattern_keys)
    combined_resolution = merge_mechanism_sets(section_resolution, pattern_resolution)

    inferred_mechanism_weights = _accumulate_weight_map(combined_resolution.get("mechanisms", []))
    inferred_category_weights = _accumulate_weight_map(combined_resolution.get("categories", []))
    direct_hits = _collect_direct_vitalhealth_hits(flagged_sections)

    for broad_key in GENERIC_MECHANISM_KEYS:
        if broad_key in inferred_mechanism_weights:
            inferred_mechanism_weights[broad_key] = max(0, inferred_mechanism_weights[broad_key] - 4)

    threshold = _mechanism_threshold(report)
    is_child = _is_child_report(report)

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
        system_score = 2 * len(system_names & section_title_names) if system_names & section_title_names else 0

        direct_hit = direct_hits.get(product_name)
        direct_score = 10 * int(direct_hit["direct_hits"]) if direct_hit else 0

        total_score = mechanism_score + category_score + system_score + direct_score

        if direct_hit:
            total_score += 6

        if matched_mechanisms and matched_categories:
            total_score += 3

        primary_focus = matched_mechanisms[0] if matched_mechanisms else None
        if primary_focus in SPECIFIC_FOCUS_AREAS:
            total_score += 2
        if primary_focus in GENERIC_FOCUS_AREAS:
            total_score -= 3

        total_score += _product_mode_adjustment(
            product_name=product_name,
            report=report,
            mode="vitalhealth_clinical_optimised",
        )

        if is_child and product_name in CHILD_EXCLUDED_PRODUCTS:
            continue

        if is_child and not direct_hit:
            total_score -= 4

        if not direct_hit and total_score < threshold:
            continue

        if not direct_hit:
            if len(matched_mechanisms) < 2 and len(matched_categories) < 1:
                continue

        supporting_sections = (
            sorted(direct_hit["supporting_sections"])[:4]
            if direct_hit
            else flagged_section_names[:4]
        )
        supporting_markers = (
            sorted(direct_hit["supporting_markers"])[:5]
            if direct_hit
            else _build_supporting_markers_fallback(flagged_sections, limit=5)
        )



        # --- CHILD-SPECIFIC FILTER: require broader evidence ---
        if is_child:
            supporting_system_count = len(supporting_sections or [])
            supporting_marker_count = len(supporting_markers or [])

            # Reject narrowly supported products (e.g. ADHD-only like V-ITALAY)
            if supporting_system_count < 2 and supporting_marker_count < 3:
                continue


        rationale_parts: List[str] = []
        if supporting_sections:
            rationale_parts.append(
                f"Suggested because related findings appear across {', '.join(supporting_sections[:4])}"
            )
        if matched_mechanisms:
            pretty_mechs = ", ".join(matched_mechanisms[:3]).replace("_", " ")
            rationale_parts.append(f"with strongest alignment to {pretty_mechs}")

        rationale = (
            " ".join(rationale_parts).strip().rstrip(".") + "."
            if rationale_parts
            else "Suggested from the scan pattern and section evidence."
        )

        results.append(
            {
                "name": product_name,
                "source": "vitalhealth",
                "source_label": display_source_label("vitalhealth"),
                "supporting_sections": supporting_sections,
                "supporting_markers": supporting_markers,
                "pattern_alignment": ", ".join(matched_categories[:2]).replace("_", " ") if matched_categories else None,
                "focus_area": primary_focus,
                "is_primary": False,
                "rationale": rationale,
                "_score": total_score,
            }
        )

    results = _apply_context_filters(results, clinical_context or {})
    results = _apply_context_boosts(results, clinical_context or {})
    results.sort(key=lambda x: (-x["_score"], x["name"]))
    return results


def resolve_vitalhealth_products_hybrid(
    report: Any,
    clinical_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    legacy_products = resolve_vitalhealth_products_legacy(report)
    mechanism_products = resolve_vitalhealth_products_mechanism(report, clinical_context=clinical_context)

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

        existing["supporting_sections"] = sorted(
            set(existing.get("supporting_sections", [])) | set(product.get("supporting_sections", []))
        )
        existing["supporting_markers"] = sorted(
            set(existing.get("supporting_markers", [])) | set(product.get("supporting_markers", []))
        )

        if not existing.get("pattern_alignment") and product.get("pattern_alignment"):
            existing["pattern_alignment"] = product.get("pattern_alignment")
        if not existing.get("focus_area") and product.get("focus_area"):
            existing["focus_area"] = product.get("focus_area")

        if source_mode == "legacy":
            existing["_legacy_hit"] = True
            existing["_score"] += 50
        elif source_mode == "mechanism":
            existing["_mechanism_hit"] = True
            existing["_score"] += int(product.get("_score", 0))

        if len(product.get("rationale", "")) > len(existing.get("rationale", "")):
            existing["rationale"] = product.get("rationale", "")

    for p in legacy_products:
        add_product(p, "legacy")

    for p in mechanism_products:
        add_product(p, "mechanism")

    results = list(product_map.values())
    is_child = _is_child_report(report)

    filtered: List[Dict[str, Any]] = []
    for item in results:
        name = item.get("name", "")
        score = int(item.get("_score", 0) or 0)
        score += _product_mode_adjustment(
            product_name=name,
            report=report,
            mode="mixed_clinical",
        )
        item["_score"] = score

        if is_child and name in CHILD_EXCLUDED_PRODUCTS:
            continue

        keep = False
        if item["_legacy_hit"] and item["_mechanism_hit"]:
            keep = True
        elif item["_mechanism_hit"] and score >= (_mechanism_threshold(report) + 6):
            keep = True
        elif item["_legacy_hit"] and score >= 100:
            keep = True

        if keep:
            filtered.append(item)

    filtered = _apply_context_filters(filtered, clinical_context or {})
    filtered = _apply_context_boosts(filtered, clinical_context or {})
    filtered.sort(key=lambda x: (-x["_score"], x["name"]))

    for item in filtered:
        sections = item.get("supporting_sections", [])[:4]
        focus = item.get("focus_area")
        if sections and focus:
            item["rationale"] = (
                f"Suggested because related findings appear across {', '.join(sections)}, "
                f"with strongest alignment to {str(focus).replace('_', ' ')}."
            )

    return filtered


def resolve_vitalhealth_products(
    report: Any,
    mode: str | None = None,
    clinical_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    final_mode = normalize_recommendation_mode(mode)
    context = normalize_clinical_context(clinical_context)

    if final_mode == "recommendations_off":
        return []

    if final_mode == "affiliate_vitalhealth":
        results = resolve_vitalhealth_products_legacy(report)
    elif final_mode == "vitalhealth_clinical_optimised":
        results = resolve_vitalhealth_products_mechanism(report, clinical_context=context)
    elif final_mode == "mixed_clinical":
        results = resolve_vitalhealth_products_hybrid(report, clinical_context=context)
    else:
        results = []

    results = _apply_vitalhealth_stack_rules(results, mode=final_mode)
    results = _cap_vitalhealth_results(results, report=report, mode=final_mode)

    for item in results:
        item.pop("_score", None)
        item.pop("_legacy_hit", None)
        item.pop("_mechanism_hit", None)

    return results


def resolve_na_products(report: Any, mode: str | None = None) -> List[Dict[str, Any]]:
    final_mode = normalize_recommendation_mode(mode)

    if final_mode in {"recommendations_off", "affiliate_vitalhealth", "vitalhealth_clinical_optimised"}:
        return []

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


def resolve_custom_practitioner_products(clinical_context: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    context = normalize_clinical_context(clinical_context)
    results: List[Dict[str, Any]] = []

    for item in context.get("custom_recommendations", []):
        name = item.get("name")
        if not name:
            continue

        results.append(
            {
                "name": name,
                "source": "custom",
                "source_label": display_source_label("custom_practitioner"),
                "supporting_sections": [],
                "supporting_markers": [],
                "pattern_alignment": None,
                "focus_area": item.get("focus_area") or None,
                "is_primary": False,
                "rationale": item.get("notes") or "Custom practitioner recommendation.",
            }
        )

    return results


def resolve_all_products(
    report: Any,
    recommendation_mode: str | None = None,
    clinical_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    final_mode = normalize_recommendation_mode(recommendation_mode or DEFAULT_RECOMMENDATION_MODE)
    context = normalize_clinical_context(clinical_context)

    if not products_enabled(final_mode):
        return []

    vitalhealth_products = resolve_vitalhealth_products(report, mode=final_mode, clinical_context=context)
    na_products = resolve_na_products(report, mode=final_mode)
    custom_products = resolve_custom_practitioner_products(context)

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
        existing["supporting_sections"] = sorted(
            set(existing.get("supporting_sections", [])) | set(product.get("supporting_sections", []))
        )
        existing["supporting_markers"] = sorted(
            set(existing.get("supporting_markers", [])) | set(product.get("supporting_markers", []))
        )

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
    for p in custom_products:
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