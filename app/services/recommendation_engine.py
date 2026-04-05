from __future__ import annotations

from typing import Any

from app.services.config_service import load_practitioner_config
from app.services.product_profile_loader import load_product_profile


def _safe_getattr(obj: Any, name: str, default=None):
    return getattr(obj, name, default)


def _safe_setattr(obj: Any, name: str, value):
    try:
        setattr(obj, name, value)
    except Exception:
        pass


def _normalise(text: str | None) -> str:
    return (text or "").strip().lower()


def _listify(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _get_recommendation_mode(config: dict) -> dict:
    mode = config.get("recommendation_mode") or {}
    return {
        "enable_clinical_recommendations": mode.get("enable_clinical_recommendations", True),
        "enable_product_recommendations": mode.get("enable_product_recommendations", False),
        "enable_vital_health": mode.get("enable_vital_health", False),
        "enable_practitioner_override": mode.get("enable_practitioner_override", True),
        "product_profile": mode.get("product_profile", "natural_approaches"),
        "max_clinical_recommendations": mode.get("max_clinical_recommendations", 6),
        "max_product_recommendations": mode.get("max_product_recommendations", 8),
    }


def _rank_section(section) -> tuple:
    return (_safe_getattr(section, "section_score", 0), _safe_getattr(section, "abnormal_count", 0))


def _severity_weight(marker) -> int:
    severity = _safe_getattr(marker, "severity", None) or "unknown"
    weights = {
        "high_severe": 6,
        "low_severe": 6,
        "high_moderate": 5,
        "low_moderate": 5,
        "high_mild": 3,
        "low_mild": 3,
        "normal": 0,
        "unknown": 1,
    }
    return weights.get(severity, 1)


def _collect_top_sections(report) -> list:
    sections = list(_safe_getattr(report, "sections", []) or [])
    sections = [s for s in sections if (_safe_getattr(s, "abnormal_count", 0) > 0)]
    return sorted(sections, key=_rank_section, reverse=True)


def _marker_matches_rule(marker, rule: dict) -> bool:
    marker_name = _normalise(_safe_getattr(marker, "source_name", ""))
    display_name = _normalise(_safe_getattr(marker, "display_label", ""))
    pattern_cluster = _normalise(_safe_getattr(marker, "pattern_cluster", ""))
    canonical_system = _normalise(_safe_getattr(marker, "canonical_system", ""))
    section_title = _normalise(_safe_getattr(marker, "source_title", ""))

    marker_names = [_normalise(x) for x in _listify(rule.get("marker_names"))]
    pattern_clusters = [_normalise(x) for x in _listify(rule.get("pattern_clusters"))]
    canonical_systems = [_normalise(x) for x in _listify(rule.get("canonical_systems"))]
    section_titles = [_normalise(x) for x in _listify(rule.get("section_titles"))]

    if marker_names and not any(name in {marker_name, display_name} for name in marker_names):
        return False
    if pattern_clusters and pattern_cluster not in pattern_clusters:
        return False
    if canonical_systems and canonical_system not in canonical_systems:
        return False
    if section_titles and section_title not in section_titles:
        return False

    min_severity = rule.get("min_severity")
    if min_severity:
        order = {"normal": 0, "mild": 1, "moderate": 2, "severe": 3}
        marker_severity = _safe_getattr(marker, "severity", "") or ""
        if "severe" in marker_severity:
            current = 3
        elif "moderate" in marker_severity:
            current = 2
        elif "mild" in marker_severity:
            current = 1
        else:
            current = 0
        if current < order.get(_normalise(min_severity), 0):
            return False

    return True


def _default_clinical_recommendations(report, max_items: int) -> list[dict]:
    sections = _collect_top_sections(report)
    recommendations: list[dict] = []

    for section in sections:
        title = _safe_getattr(section, "display_title", None) or _safe_getattr(section, "source_title", "This section")
        abnormal_count = _safe_getattr(section, "abnormal_count", 0)
        top_markers = sorted(list(_safe_getattr(section, "parameters", []) or []), key=_severity_weight, reverse=True)[:3]

        marker_names = []
        for marker in top_markers:
            if _severity_weight(marker) > 0:
                marker_names.append(_safe_getattr(marker, "display_label", None) or _safe_getattr(marker, "source_name", ""))

        if "gastro" in title.lower() or "intestine" in title.lower():
            summary = "Support digestive function, gut motility, and nutrient absorption so downstream inflammation and energy strain are not perpetuated."
            focus = "Gut function"
        elif "cardio" in title.lower() or "cerebro" in title.lower() or "vascular" in title.lower():
            summary = "Support vascular resilience, circulation, and cardiac efficiency while reviewing the lifestyle and inflammatory factors shaping cardiovascular load."
            focus = "Cardiovascular support"
        elif "vitamin" in title.lower() or "trace" in title.lower() or "amino" in title.lower() or "coenzyme" in title.lower():
            summary = "Address nutrient sufficiency and absorption so foundational antioxidant, mitochondrial, and repair pathways can function more effectively."
            focus = "Nutrient repletion"
        elif "skin" in title.lower() or "allergy" in title.lower():
            summary = "Review barrier integrity, inflammatory triggers, and exposure load to reduce immune irritation and improve tissue resilience."
            focus = "Inflammation and barrier support"
        elif "heavy metal" in title.lower() or "toxin" in title.lower() or "liver" in title.lower():
            summary = "Reduce exposure burden and support detoxification capacity while maintaining bowel regularity, hydration, and antioxidant defence."
            focus = "Detoxification support"
        else:
            summary = f"Provide targeted support for {title.lower()} patterns and review the broader drivers contributing to repeated abnormalities in this area."
            focus = title

        recommendations.append({
            "title": focus,
            "summary": summary,
            "rationale": f"{title} contains {abnormal_count} flagged markers" + (f", led by {', '.join(marker_names)}." if marker_names else "."),
            "source": "system_default",
            "section": title,
            "priority_rank": len(recommendations) + 1,
        })

        if len(recommendations) >= max_items:
            break

    return recommendations


def _rules_based_clinical_recommendations(report, rules: dict, max_items: int) -> list[dict]:
    section_rules = rules.get("clinical_recommendations", []) or []
    generated: list[dict] = []
    seen_titles = set()

    for section in _collect_top_sections(report):
        section_title = _safe_getattr(section, "display_title", None) or _safe_getattr(section, "source_title", "")
        markers = list(_safe_getattr(section, "parameters", []) or [])

        for rule in section_rules:
            applies_to_section = False
            section_titles = [_normalise(x) for x in _listify(rule.get("section_titles"))]
            if section_titles and _normalise(section_title) in section_titles:
                applies_to_section = True
            else:
                for marker in markers:
                    if _marker_matches_rule(marker, rule):
                        applies_to_section = True
                        break

            if not applies_to_section:
                continue

            title = rule.get("title") or "Clinical recommendation"
            if title in seen_titles:
                continue

            generated.append({
                "title": title,
                "summary": rule.get("summary", ""),
                "rationale": rule.get("rationale", f"Triggered by {section_title}."),
                "source": "system_default",
                "section": section_title,
                "priority_rank": len(generated) + 1,
            })
            seen_titles.add(title)

            if len(generated) >= max_items:
                return generated

    return generated


def generate_clinical_recommendations(report, max_items: int, clinical_rules: dict) -> list[dict]:
    rules_based = _rules_based_clinical_recommendations(report, clinical_rules, max_items=max_items)
    if rules_based:
        return rules_based[:max_items]
    return _default_clinical_recommendations(report, max_items=max_items)


def _build_product_lookup(catalog: dict) -> dict:
    products = catalog.get("products", []) or []
    return {str(p.get("id") or p.get("sku") or p.get("name")): p for p in products}


def generate_product_recommendations(report, rules: dict, catalog: dict, max_items: int) -> list[dict]:
    product_lookup = _build_product_lookup(catalog)
    product_rules = rules.get("product_recommendations", []) or []
    product_hits: list[dict] = []
    seen_product_keys = set()

    for section in _collect_top_sections(report):
        markers = list(_safe_getattr(section, "parameters", []) or [])
        section_title = _safe_getattr(section, "display_title", None) or _safe_getattr(section, "source_title", "")

        for rule in product_rules:
            applies = False
            section_titles = [_normalise(x) for x in _listify(rule.get("section_titles"))]
            if section_titles and _normalise(section_title) in section_titles:
                applies = True
            else:
                for marker in markers:
                    if _marker_matches_rule(marker, rule):
                        applies = True
                        break

            if not applies:
                continue

            for product_id in _listify(rule.get("product_ids")):
                product = product_lookup.get(str(product_id))
                if not product:
                    continue

                dedupe_key = str(product.get("id") or product.get("sku") or product.get("name"))
                if dedupe_key in seen_product_keys:
                    continue

                product_hits.append({
                    "id": product.get("id"),
                    "sku": product.get("sku"),
                    "name": product.get("name"),
                    "brand": product.get("brand"),
                    "category": product.get("category"),
                    "summary": product.get("summary") or rule.get("product_summary") or "",
                    "patient_note": rule.get("patient_note") or product.get("patient_note") or "",
                    "reasons": [rule.get("rationale", f"Triggered by {section_title}.")],
                    "url": product.get("url"),
                    "source": catalog.get("profile_name", "unknown"),
                })
                seen_product_keys.add(dedupe_key)

                if len(product_hits) >= max_items:
                    return product_hits

    return product_hits


def apply_practitioner_overrides(report):
    return report


def apply_recommendation_engine(report):
    config = load_practitioner_config()
    mode = _get_recommendation_mode(config)

    clinical_rules = {}
    try:
        _, natural_rules = load_product_profile("natural_approaches")
        clinical_rules = natural_rules
    except Exception:
        clinical_rules = {}

    if mode["enable_clinical_recommendations"]:
        _safe_setattr(report, "clinical_recommendations", generate_clinical_recommendations(report, max_items=mode["max_clinical_recommendations"], clinical_rules=clinical_rules))
    else:
        _safe_setattr(report, "clinical_recommendations", [])

    if mode["enable_product_recommendations"]:
        selected_profile = mode["product_profile"]
        if selected_profile == "vital_health" and not mode["enable_vital_health"]:
            selected_profile = "natural_approaches"

        catalog, rules = load_product_profile(selected_profile)
        _safe_setattr(report, "product_recommendations", generate_product_recommendations(report, rules=rules, catalog=catalog, max_items=mode["max_product_recommendations"]))
    else:
        _safe_setattr(report, "product_recommendations", [])

    if mode["enable_practitioner_override"]:
        report = apply_practitioner_overrides(report)

    return report
