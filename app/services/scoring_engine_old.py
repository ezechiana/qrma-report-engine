from __future__ import annotations

from typing import Any, Dict, List


SEVERITY_BANDS = {
    "normal": (90, 100),
    "low_mild": (78, 89),
    "high_mild": (78, 89),
    "low_moderate": (60, 77),
    "high_moderate": (60, 77),
    "low_severe": (35, 59),
    "high_severe": (35, 59),
    "unknown": (72, 84),
}

BAND_CONFIG = [
    (85, "Very Good", "#2e7d32"),
    (70, "General", "#1565c0"),
    (55, "Relatively Poor", "#e0b71a"),
    (0, "Very Bad", "#d6523c"),
]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _parse_range(range_text: str | None) -> tuple[float | None, float | None]:
    if not range_text:
        return None, None
    text = str(range_text).strip().replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in text.split("-")]
    if len(parts) != 2:
        return None, None
    return _safe_float(parts[0]), _safe_float(parts[1])


def _marker_position_factor(marker) -> float:
    """
    Positions a marker within its severity band.
    1.0 = near the better end of that band
    0.0 = near the worse end of that band
    """
    severity = getattr(marker, "severity", None) or "unknown"
    value = _safe_float(getattr(marker, "actual_value_numeric", None))
    low, high = _parse_range(getattr(marker, "normal_range_text", None))

    if value is None or low is None or high is None:
        return 0.5

    span = max(high - low, 1e-9)

    if severity == "normal":
        midpoint = (low + high) / 2
        half_span = max((high - low) / 2, 1e-9)
        distance = abs(value - midpoint) / half_span
        return _clamp(1.0 - (distance * 0.55), 0.0, 1.0)

    if severity.startswith("high"):
        distance = max(0.0, value - high)
        if severity == "high_mild":
            return _clamp(1.0 - (distance / (span * 1.15)), 0.0, 1.0)
        if severity == "high_moderate":
            return _clamp(0.85 - (distance / (span * 2.1)), 0.0, 1.0)
        if severity == "high_severe":
            return _clamp(0.65 - (distance / (span * 3.0)), 0.0, 1.0)

    if severity.startswith("low"):
        distance = max(0.0, low - value)
        if severity == "low_mild":
            return _clamp(1.0 - (distance / (span * 1.15)), 0.0, 1.0)
        if severity == "low_moderate":
            return _clamp(0.85 - (distance / (span * 2.1)), 0.0, 1.0)
        if severity == "low_severe":
            return _clamp(0.65 - (distance / (span * 3.0)), 0.0, 1.0)

    return 0.5


def score_marker(marker) -> int:
    severity = getattr(marker, "severity", None) or "unknown"
    low, high = SEVERITY_BANDS.get(severity, SEVERITY_BANDS["unknown"])
    factor = _marker_position_factor(marker)
    score = round(low + ((high - low) * factor))

    # small priority nudge only, not enough to distort the section
    if getattr(marker, "marker_priority", None) == "high":
        score -= 2

    return max(0, min(100, score))


def score_to_band(score: float) -> Dict[str, str]:
    for threshold, label, color in BAND_CONFIG:
        if score >= threshold:
            return {"label": label, "color": color}
    return {"label": "General", "color": "#1565c0"}


def _count_severities(parameters: List[Any]) -> Dict[str, int]:
    mild = 0
    moderate = 0
    severe = 0
    normal = 0

    for p in parameters:
        sev = (getattr(p, "severity", None) or "normal")
        if sev == "normal":
            normal += 1
        elif sev.endswith("mild"):
            mild += 1
        elif sev.endswith("moderate"):
            moderate += 1
        elif sev.endswith("severe"):
            severe += 1
        else:
            mild += 1

    return {
        "normal_count": normal,
        "mild_count": mild,
        "moderate_count": moderate,
        "severe_count": severe,
        "flagged_count": mild + moderate + severe,
        "within_count": normal,
    }


def compute_section_score(section) -> Dict[str, Any]:
    parameters = list(getattr(section, "parameters", []) or [])
    title = getattr(section, "display_title", None) or getattr(section, "source_title", "")

    if not parameters:
        band = score_to_band(0)
        return {
            "title": title,
            "score": 0,
            "band_label": band["label"],
            "gauge_color": band["color"],
            "flagged_count": 0,
            "within_count": 0,
            "total_count": 0,
            "normal_count": 0,
            "mild_count": 0,
            "moderate_count": 0,
            "severe_count": 0,
        }

    marker_scores = [score_marker(p) for p in parameters]
    base_score = sum(marker_scores) / len(marker_scores)

    counts = _count_severities(parameters)

    # Gentle clustering penalty.
    # Enough to reflect burden, but not enough to create incoherent overall results.
    penalty = (
        counts["mild_count"] * 0.35
        + counts["moderate_count"] * 0.90
        + counts["severe_count"] * 1.75
    )

    score = round(_clamp(base_score - penalty, 0, 100))
    band = score_to_band(score)

    return {
        "title": title,
        "score": score,
        "band_label": band["label"],
        "gauge_color": band["color"],
        "flagged_count": counts["flagged_count"],
        "within_count": counts["within_count"],
        "total_count": len(parameters),
        "normal_count": counts["normal_count"],
        "mild_count": counts["mild_count"],
        "moderate_count": counts["moderate_count"],
        "severe_count": counts["severe_count"],
    }


def compute_scan_scores(sections: List[Any]) -> Dict[str, Any]:
    cards = [compute_section_score(section) for section in sections if getattr(section, "parameters", None)]

    if not cards:
        band = score_to_band(0)
        return {
            "overall_score": 0,
            "overall_band_label": band["label"],
            "overall_gauge_color": band["color"],
            "system_score_cards": [],
        }

    total_markers = sum(card["total_count"] for card in cards) or 1

    # Primary overall model: weighted average of section scores
    weighted_average = sum(card["score"] * card["total_count"] for card in cards) / total_markers

    # Very gentle global burden adjustment
    total_mild = sum(card["mild_count"] for card in cards)
    total_moderate = sum(card["moderate_count"] for card in cards)
    total_severe = sum(card["severe_count"] for card in cards)

    burden_penalty = (
        total_mild * 0.03
        + total_moderate * 0.08
        + total_severe * 0.15
    )

    overall_score = round(_clamp(weighted_average - burden_penalty, 0, 100))

    # Guardrail: overall score should not collapse far below all displayed categories
    min_section_score = min(card["score"] for card in cards)
    lower_guardrail = max(0, min_section_score - 5)
    overall_score = max(overall_score, lower_guardrail)

    overall_band = score_to_band(overall_score)

    return {
        "overall_score": overall_score,
        "overall_band_label": overall_band["label"],
        "overall_gauge_color": overall_band["color"],
        "system_score_cards": sorted(cards, key=lambda c: c["score"]),
    }