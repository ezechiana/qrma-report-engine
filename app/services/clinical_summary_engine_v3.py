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
    """
    Role:
    - high-level scan interpretation
    - concise
    - says WHAT the scan most suggests
    - should avoid sounding like an action plan
    """
    if not primary_pattern:
        return (
            "The scan suggests a mixed functional picture without a single dominant driver "
            "clearly outweighing the others."
        )

    label = _pattern_label(primary_pattern).lower()
    sections = _top_sections_from_pattern(primary_pattern, limit=3)
    markers = _top_marker_names_from_pattern(primary_pattern, limit=4)
    strength_phrase = _driver_strength_phrase(primary_pattern)

    section_text = _human_join(sections)
    marker_text = _human_join(markers)

    if section_text and marker_text:
        return (
            f"The overall scan pattern {strength_phrase} {label}, with the clearest clustering across "
            f"{section_text}. Key supporting signals include {marker_text}."
        )

    if section_text:
        return (
            f"The overall scan pattern {strength_phrase} {label}, with the clearest clustering across "
            f"{section_text}."
        )

    if marker_text:
        return (
            f"The overall scan pattern {strength_phrase} {label}, supported by key signals including "
            f"{marker_text}."
        )

    return f"The overall scan pattern {strength_phrase} {label}."


def _build_secondary_driver_text(secondary_patterns: list[Any]) -> str:
    if not secondary_patterns:
        return (
            "No strong secondary drivers clearly separate from the dominant pattern at this stage."
        )

    snippets = []
    for pattern in secondary_patterns[:3]:
        label = _pattern_label(pattern).lower()
        markers = _top_marker_names_from_pattern(pattern, limit=3)

        if markers:
            snippets.append(f"{label} (linked to { _human_join(markers) })")
        else:
            snippets.append(label)

    return (
        f"Additional contributing patterns include { _human_join(snippets) }. "
        f"These are likely reinforcing the primary imbalance rather than acting independently."
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
            "The interpretation is based on a consistent pattern of abnormalities across multiple "
            "systems rather than any single isolated marker."
        )

    return (
        f"The strongest signals within this scan come from { _human_join(markers) }, "
        f"which together help define the overall functional pattern."
    )


def _pattern_kind(pattern: Any) -> str:
    return str(_safe_get(pattern, "kind", "") or "").strip().lower()


def _build_why_this_matters_text(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    """
    Role:
    - consequence / downstream impact
    - says WHY the dominant pattern matters
    - should translate mechanism into practical clinical relevance
    """
    if not primary_pattern:
        return (
            "Because several systems may be contributing at the same time, the most useful next step "
            "is to identify the dominant driver and address that first rather than trying to intervene "
            "everywhere at once."
        )

    kind = _pattern_kind(primary_pattern)
    secondary_labels = [_pattern_label(p).lower() for p in secondary_patterns[:2]]
    secondary_text = _human_join(secondary_labels)

    if kind == "absorption_assimilation":
        base = (
            "This matters because reduced digestive and absorptive efficiency can translate into poorer "
            "nutrient availability, slower repair, reduced resilience, and a tendency for other systems "
            "to become more reactive over time."
        )
    elif kind == "toxic_burden":
        base = (
            "This matters because a higher toxic-load pattern can increase the burden on clearance pathways, "
            "amplify inflammatory pressure, and reduce overall physiological resilience."
        )
    elif kind == "inflammatory_barrier":
        base = (
            "This matters because barrier dysfunction and inflammatory stress can sustain irritation, "
            "increase immune reactivity, and make recovery slower or less stable."
        )
    elif kind == "neurocognitive_support":
        base = (
            "This matters because reduced neurocognitive support can affect regulation, stress tolerance, "
            "mental clarity, and day-to-day functional capacity."
        )
    elif kind == "mitochondrial_energy":
        base = (
            "This matters because reduced energy-production efficiency can contribute to fatigue, slower "
            "recovery, lower metabolic resilience, and reduced reserve under stress."
        )
    elif kind == "glycaemic_metabolic":
        base = (
            "This matters because metabolic dysregulation can destabilise energy, reinforce inflammatory "
            "load, and influence cardiovascular and hormonal balance over time."
        )
    elif kind == "lipid_transport_membrane":
        base = (
            "This matters because weaker membrane and lipid-transport support can affect signalling, "
            "neurological resilience, inflammatory balance, and nutrient delivery."
        )
    elif kind == "connective_tissue_repair":
        base = (
            "This matters because reduced connective-tissue and repair support can slow recovery, reduce "
            "structural resilience, and contribute to ongoing fragility or irritation."
        )
    else:
        base = (
            "This matters because the dominant imbalance is likely to have wider downstream effects beyond "
            "the individual markers themselves."
        )

    if secondary_text:
        return f"{base} It may also help explain the overlap with {secondary_text}."

    return base


def _build_priority_actions_text(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    patterns = [p for p in [primary_pattern, *secondary_patterns] if p]
    actions = _top_actions(patterns, limit=5)

    if not actions:
        return (
            "Intervention should focus first on the dominant functional imbalance, with "
            "secondary patterns addressed once initial response is established."
        )

    return (
        f"From a clinical perspective, the most relevant next steps are to focus on "
        f"{ _human_join(actions) }. Addressing these areas is likely to produce the "
        f"greatest overall shift in the current pattern."
    )

def _build_practitioner_summary(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    """
    Role:
    - clinical prioritisation
    - says WHAT TO DO FIRST
    - should sound like sequencing, not just interpretation
    """
    primary_label = _pattern_label(primary_pattern).lower() if primary_pattern else "a mixed functional imbalance"
    primary_markers = _top_marker_names_from_pattern(primary_pattern, limit=4) if primary_pattern else []
    secondary_labels = [_pattern_label(p).lower() for p in secondary_patterns[:3]]

    marker_text = _human_join(primary_markers)
    secondary_text = _human_join(secondary_labels)

    if primary_markers and secondary_labels:
        return (
            f"Clinical priority should be given first to {primary_label}, particularly in light of "
            f"{marker_text}. Secondary contributors such as {secondary_text} are likely to improve "
            f"more effectively once the dominant driver is addressed."
        )

    if primary_markers:
        return (
            f"Clinical priority should be given first to {primary_label}, particularly in light of "
            f"{marker_text}. Sequencing should begin here before broadening to less dominant findings."
        )

    return (
        f"Clinical priority should be given first to {primary_label}. Sequencing should begin with "
        f"the dominant driver before extending support more widely."
    )

def _build_patient_summary(primary_pattern: Any, secondary_patterns: list[Any]) -> str:
    """
    Role:
    - plain-language patient-facing explanation
    - simpler than practitioner overview
    - should explain why focus matters without sounding technical
    """
    primary_label = _pattern_label(primary_pattern).lower() if primary_pattern else "a mixed functional imbalance"
    secondary_labels = [_pattern_label(p).lower() for p in secondary_patterns[:2]]

    if secondary_labels:
        return (
            f"The scan points mainly toward {primary_label}, with some additional contribution from "
            f"{_human_join(secondary_labels)}. In practical terms, this means support is likely to work "
            f"best when the main imbalance is addressed first."
        )

    return (
        f"The scan points mainly toward {primary_label}. In practical terms, this means the most useful "
        f"next step is to focus on that main pattern first rather than trying to target everything at once."
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
    why_this_matters = _build_why_this_matters_text(primary_pattern, secondary_patterns)
    priority_actions = _build_priority_actions_text(primary_pattern, secondary_patterns)

    practitioner_summary = _build_practitioner_summary(primary_pattern, secondary_patterns)
    patient_summary = _build_patient_summary(primary_pattern, secondary_patterns)

    summary_payload = {
        "dominant_driver": dominant_driver,
        "secondary_drivers": secondary_driver,
        "key_marker_evidence": marker_evidence,
        "why_this_matters": why_this_matters,
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