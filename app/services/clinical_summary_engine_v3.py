from __future__ import annotations

from typing import Any, Iterable


def _safe_get(obj: Any, attr: str, default: Any = None) -> Any:
    return getattr(obj, attr, default)


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _human_join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _pattern_label(pattern: Any) -> str:
    return (
        _safe_get(pattern, "label", None)
        or _safe_get(pattern, "name", None)
        or _safe_get(pattern, "title", None)
        or "Unspecified pattern"
    )


def _pattern_score(pattern: Any) -> float:
    try:
        return float(_safe_get(pattern, "score", 0) or 0)
    except Exception:
        return 0.0


def _pattern_confidence(pattern: Any) -> str:
    return str(_safe_get(pattern, "confidence", "medium") or "medium").strip().lower()


def _pattern_severity(pattern: Any) -> str:
    return str(_safe_get(pattern, "severity", "moderate") or "moderate").strip().lower()


def _pattern_evidence(pattern: Any) -> list[Any]:
    return list(_safe_get(pattern, "evidence", []) or [])


def _top_marker_names_from_pattern(pattern: Any, limit: int = 4) -> list[str]:
    names: list[str] = []
    for ev in _pattern_evidence(pattern):
        marker = _safe_get(ev, "marker", None)
        if marker:
            names.append(str(marker).strip())
    return _dedupe_keep_order(names)[:limit]


def _top_sections_from_pattern(pattern: Any, limit: int = 3) -> list[str]:
    sections: list[str] = []
    metadata = _safe_get(pattern, "metadata", {}) or {}
    matched = metadata.get("matched_sections", []) or []
    for section in matched:
        if section:
            sections.append(str(section).strip())
    if not sections:
        for ev in _pattern_evidence(pattern):
            section = _safe_get(ev, "section", None)
            if section:
                sections.append(str(section).strip())
    return _dedupe_keep_order(sections)[:limit]


def _top_actions(patterns: list[Any], limit: int = 5) -> list[str]:
    actions: list[str] = []
    for pattern in patterns:
        actions.extend(list(_safe_get(pattern, "suggested_focus_areas", []) or []))
    return _dedupe_keep_order(actions)[:limit]


def _driver_strength_phrase(pattern: Any) -> str:
    severity = _pattern_severity(pattern)
    confidence = _pattern_confidence(pattern)
    score = _pattern_score(pattern)

    if severity == "high" and confidence == "high" and score >= 75:
        return "strongly indicates"
    if severity == "high":
        return "indicates"
    if severity == "moderate" and confidence in {"high", "medium"}:
        return "is consistent with"
    return "may reflect an element of"



def _build_dominant_driver_text(primary_pattern: Any) -> str:
    if not primary_pattern:
        return (
            "The scan suggests a mixed functional picture without a single dominant driver clearly "
            "outweighing the others."
        )

    label = _pattern_label(primary_pattern)
    sections = _top_sections_from_pattern(primary_pattern, limit=3)
    markers = _top_marker_names_from_pattern(primary_pattern, limit=4)
    strength_phrase = _driver_strength_phrase(primary_pattern)

    section_text = _human_join(sections)
    marker_text = _human_join(markers)

    if section_text and marker_text:
        return (
            f"The overall picture {strength_phrase} {label.lower()}, anchored mainly in "
            f"{section_text}, with key signals including {marker_text}."
        )

    if marker_text:
        return (
            f"The dominant pattern {strength_phrase} {label.lower()}, with key signals including "
            f"{marker_text}."
        )

    if section_text:
        return (
            f"The dominant pattern {strength_phrase} {label.lower()}, with the strongest clustering "
            f"across {section_text}."
        )

    return f"The dominant pattern {strength_phrase} {label.lower()}."


def _build_secondary_driver_text(secondary_patterns: list[Any]) -> str:
    if not secondary_patterns:
        return (
            "No strong secondary driver clearly separates from the dominant pattern at this stage."
        )

    snippets = []
    for pattern in secondary_patterns[:3]:
        label = _pattern_label(pattern)
        markers = _top_marker_names_from_pattern(pattern, limit=3)
        sections = _top_sections_from_pattern(pattern, limit=2)

        if markers:
            snippets.append(f"{label.lower()} ({_human_join(markers)})")
        elif sections:
            snippets.append(f"{label.lower()} ({_human_join(sections)})")
        else:
            snippets.append(label.lower())

    return (
        "Important contributing patterns include "
        f"{_human_join(snippets)}, which likely add to the overall functional load."
    )


def _build_marker_evidence_text(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    marker_pool: list[str] = []

    if primary_pattern:
        marker_pool.extend(_top_marker_names_from_pattern(primary_pattern, limit=5))

    for pattern in secondary_patterns[:2]:
        marker_pool.extend(_top_marker_names_from_pattern(pattern, limit=3))

    markers = _dedupe_keep_order(marker_pool)[:6]

    if not markers:
        return (
            "The strongest evidence comes from clustered abnormalities across multiple sections "
            "rather than from a single isolated marker."
        )

    return (
        "The most useful markers for anchoring interpretation in this scan are "
        f"{_human_join(markers)}."
    )


def _build_priority_actions_text(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    patterns = [p for p in [primary_pattern, *secondary_patterns] if p]
    actions = _top_actions(patterns, limit=5)

    if not actions:
        return (
            "Priority actions should focus on the dominant driver first, then address the most "
            "consistent secondary burdens."
        )

    return (
        "The most relevant immediate focus areas are "
        f"{_human_join(actions)}."
    )


def _build_practitioner_summary(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    primary_label = _pattern_label(primary_pattern) if primary_pattern else "mixed functional imbalance"
    primary_markers = _top_marker_names_from_pattern(primary_pattern, limit=4) if primary_pattern else []
    secondary_labels = [_pattern_label(p).lower() for p in secondary_patterns[:3]]

    marker_text = _human_join(primary_markers)
    secondary_text = _human_join(secondary_labels)

    if primary_markers and secondary_labels:
        return (
            f"Primary driver appears to be {primary_label.lower()}, supported by {marker_text}. "
            f"Secondary contributions include {secondary_text}. Prioritise intervention around the "
            f"dominant driver first, then reassess the secondary patterns as resilience improves."
        )

    if primary_markers:
        return (
            f"Primary driver appears to be {primary_label.lower()}, supported by {marker_text}. "
            f"Clinical sequencing should begin with this cluster."
        )

    return (
        f"Primary driver appears to be {primary_label.lower()}. Clinical sequencing should begin "
        f"with the dominant cluster and then broaden only if follow-up findings justify it."
    )


def _build_patient_summary(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    primary_label = _pattern_label(primary_pattern).lower() if primary_pattern else "a mixed functional imbalance"
    secondary_labels = [_pattern_label(p).lower() for p in secondary_patterns[:2]]

    if secondary_labels:
        return (
            f"The scan points mainly toward {primary_label}, with additional contribution from "
            f"{_human_join(secondary_labels)}. This means support is likely to work best when the "
            f"main driver is addressed first rather than trying to target everything at once."
        )

    return (
        f"The scan points mainly toward {primary_label}. This suggests the best next step is to "
        f"focus on the dominant imbalance first and build from there."
    )


def build_clinical_summary_v3(report: Any) -> Any:
    """
    Non-destructive V3 clinical summary layer.

    Writes new summary fields while preserving compatibility with existing
    downstream report/template usage.
    """
    primary_pattern = _safe_get(report, "primary_pattern", None)
    secondary_patterns = list(_safe_get(report, "contributing_patterns", []) or [])

    dominant_driver = _build_dominant_driver_text(primary_pattern)
    secondary_driver = _build_secondary_driver_text(secondary_patterns)
    marker_evidence = _build_marker_evidence_text(primary_pattern, secondary_patterns)
    priority_actions = _build_priority_actions_text(primary_pattern, secondary_patterns)

    practitioner_summary = _build_practitioner_summary(primary_pattern, secondary_patterns)
    patient_summary = _build_patient_summary(primary_pattern, secondary_patterns)

    summary_payload = {
        "dominant_driver": dominant_driver,
        "secondary_drivers": secondary_driver,
        "key_marker_evidence": marker_evidence,
        "priority_actions": priority_actions,
        "practitioner_summary": practitioner_summary,
        "patient_summary": patient_summary,
    }

    # New V3 payload
    report.clinical_summary_v3 = summary_payload

    # Backward-friendly assignments for existing report consumers
    report.overall_summary = dominant_driver
    report.practitioner_summary = practitioner_summary
    report.priority_actions = _top_actions([p for p in [primary_pattern, *secondary_patterns] if p], limit=5)

    return report