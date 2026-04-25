# app/services/recommendation_trend_narrative.py

from __future__ import annotations

from typing import Any, Dict, List


FLAG_LABELS = {
    "meaningful_change": "meaningful marker movement",
    "band_shift": "classification band shift",
    "multi_system": "multi-system support pattern",
    "largest_increase": "notable upward trend",
    "largest_decrease": "notable downward trend",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalise(value: str | None) -> str:
    return _safe_text(value).lower().replace("_", " ").strip()


def _clean_marker_label(value: str | None) -> str:
    text = _safe_text(value)
    for suffix in (" Significant Band", " Significant"):
        if text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)]
    return " ".join(text.split())


def _category(item: Dict[str, Any]) -> str:
    return (
        _safe_text(item.get("category"))
        or _safe_text(item.get("category_name"))
        or _safe_text(item.get("qrma_category"))
        or _safe_text(item.get("system"))
        or _safe_text(item.get("group"))
        or "General"
    )


def _direction_phrase(direction: str | None) -> str:
    if direction == "up":
        return "increased"
    if direction == "down":
        return "decreased"
    if direction == "flat":
        return "remained broadly stable"
    return "changed"


def _delta_text(delta: Any) -> str:
    try:
        value = float(delta)
    except Exception:
        return ""
    return f"{value:+.3f}".rstrip("0").rstrip(".")


def _trend_matches_product(product: Dict[str, Any], trend_item: Dict[str, Any]) -> bool:
    """
    Soft matching only. We do not create recommendations from this.
    We only look for trend evidence that supports an already-resolved recommendation.
    """
    marker_label = _normalise(trend_item.get("label"))
    marker_key = _normalise(trend_item.get("key"))
    category = _normalise(_category(trend_item))

    supporting_markers = [_normalise(x) for x in product.get("supporting_markers", []) or []]
    supporting_sections = [_normalise(x) for x in product.get("supporting_sections", []) or []]
    focus_area = _normalise(product.get("focus_area"))
    pattern_alignment = _normalise(product.get("pattern_alignment"))
    rationale = _normalise(product.get("rationale"))

    haystacks = supporting_markers + supporting_sections + [focus_area, pattern_alignment, rationale]

    for h in haystacks:
        if not h:
            continue
        if marker_label and (marker_label in h or h in marker_label):
            return True
        if marker_key and (marker_key in h or h in marker_key):
            return True
        if category and (category in h or h in category):
            return True

    return False


def _top_trend_evidence_for_product(
    product: Dict[str, Any],
    trend_summary: List[Dict[str, Any]],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []

    for item in trend_summary or []:
        if item.get("has_trend") is not True:
            continue
        if item.get("is_meaningful") is not True and item.get("band_changed") is not True:
            continue
        if _trend_matches_product(product, item):
            matches.append(item)

    def score(item: Dict[str, Any]) -> float:
        try:
            delta_score = abs(float(item.get("delta") or 0))
        except Exception:
            delta_score = 0
        if item.get("band_changed") is True:
            delta_score += 1000
        if item.get("is_meaningful") is True:
            delta_score += 100
        return delta_score

    matches.sort(key=score, reverse=True)
    return matches[:limit]


def _evidence_sentence(evidence: List[Dict[str, Any]]) -> str:
    if not evidence:
        return ""

    parts = []
    for item in evidence[:3]:
        label = _clean_marker_label(item.get("label"))
        category = _category(item)
        direction = _direction_phrase(item.get("direction"))
        delta = _delta_text(item.get("delta"))

        if delta:
            parts.append(f"{label} ({category}) {direction} by {delta}")
        else:
            parts.append(f"{label} ({category}) {direction}")

    if len(parts) == 1:
        return f"Trend evidence: {parts[0]}."
    return "Trend evidence: " + "; ".join(parts) + "."


def _priority_sentence(product: Dict[str, Any], evidence: List[Dict[str, Any]]) -> str:
    priority = product.get("trend_priority") or "low"
    flags = set(product.get("trend_flags") or [])

    if priority == "high":
        if "band_shift" in flags:
            return (
                "This deserves higher practitioner attention because one or more related markers "
                "changed classification band across scans."
            )
        return (
            "This deserves higher practitioner attention because related markers show meaningful "
            "movement across the available scan history."
        )

    if priority == "medium":
        return (
            "This may be clinically relevant as a supporting consideration because related findings "
            "show a measurable trend signal."
        )

    return (
        "This remains a lower-priority support consideration unless it aligns with symptoms, history, "
        "or practitioner assessment."
    )


def _safety_sentence(product: Dict[str, Any]) -> str:
    source = _safe_text(product.get("source_label") or product.get("source"))
    if source:
        return (
            f"Review suitability, contraindications, allergies, current medicines, and clinical context "
            f"before using this {source} recommendation."
        )

    return (
        "Review suitability, contraindications, allergies, current medicines, and clinical context "
        "before using this recommendation."
    )


def build_recommendation_narrative(
    product: Dict[str, Any],
    trend_summary: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Adds trend-aware narrative fields to an already-resolved recommendation.

    This function does not create products, does not prescribe, and does not bypass the
    existing recommendation mode, product resolver, or safety filters.
    """
    item = dict(product)
    evidence = _top_trend_evidence_for_product(item, trend_summary)

    original_rationale = _safe_text(item.get("rationale"))
    evidence_text = _evidence_sentence(evidence)
    priority_text = _priority_sentence(item, evidence)
    safety_text = _safety_sentence(item)

    explanation_parts = []
    if original_rationale:
        explanation_parts.append(original_rationale)
    if evidence_text:
        explanation_parts.append(evidence_text)
    explanation_parts.append(priority_text)

    item["trend_narrative"] = {
        "summary": " ".join(explanation_parts),
        "why_now": priority_text,
        "trend_evidence": evidence_text,
        "safety_note": safety_text,
        "evidence_markers": [
            {
                "key": e.get("key"),
                "label": _clean_marker_label(e.get("label")),
                "category": _category(e),
                "latest": e.get("latest"),
                "delta": e.get("delta"),
                "direction": e.get("direction"),
                "status": e.get("status"),
                "band_changed": bool(e.get("band_changed")),
            }
            for e in evidence
        ],
        "review_required": True,
    }

    return item


def add_trend_narratives_to_products(
    products: List[Dict[str, Any]],
    trend_summary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        build_recommendation_narrative(product, trend_summary)
        for product in products or []
    ]


def add_trend_narratives_to_protocol(
    protocol: Dict[str, Any] | None,
    trend_summary: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    if not isinstance(protocol, dict):
        return protocol

    out = dict(protocol)
    phases = []

    for phase in out.get("phases", []) or []:
        phase_copy = dict(phase)
        phase_copy["products"] = add_trend_narratives_to_products(
            phase_copy.get("products", []) or [],
            trend_summary,
        )
        phases.append(phase_copy)

    out["phases"] = phases
    return out
