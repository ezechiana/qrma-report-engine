# app/services/rules_engine.py

from app.models.schema import ParsedReport, ReportSection, ParameterResult
from app.services.marker_definition_service import (
    load_marker_definition_index,
    get_marker_definition,
)

SEVERITY_SCORES = {
    "normal": 0,
    "low_mild": 1,
    "high_mild": 1,
    "low_moderate": 2,
    "high_moderate": 2,
    "low_severe": 3,
    "high_severe": 3,
    "unknown": 0,
}


def severity_label(severity: str | None) -> str:
    labels = {
        "normal": "within range",
        "low_mild": "mildly reduced",
        "high_mild": "mildly elevated",
        "low_moderate": "moderately reduced",
        "high_moderate": "moderately elevated",
        "low_severe": "markedly reduced",
        "high_severe": "markedly elevated",
        "unknown": "unclear",
    }
    return labels.get(severity or "unknown", "unclear")


def compute_section_score(section: ReportSection) -> int:
    return sum(SEVERITY_SCORES.get(param.severity or "unknown", 0) for param in section.parameters)


def classify_priority(score: int, abnormal_count: int) -> str:
    if score >= 6 or abnormal_count >= 4:
        return "high"
    if score >= 3 or abnormal_count >= 2:
        return "medium"
    if abnormal_count >= 1:
        return "low"
    return "normal"


def sort_abnormal_parameters(section: ReportSection) -> list[ParameterResult]:
    abnormal_params = [p for p in section.parameters if p.is_abnormal]

    def sort_key(param: ParameterResult):
        return (
            SEVERITY_SCORES.get(param.severity or "unknown", 0),
            param.marker_priority == "high",
            param.source_name.lower(),
        )

    return sorted(abnormal_params, key=sort_key, reverse=True)


def build_top_findings(section: ReportSection, max_findings: int = 3) -> list[str]:
    findings = []
    for param in sort_abnormal_parameters(section)[:max_findings]:
        findings.append(f"{param.source_name} is {severity_label(param.severity)}")
    return findings


def count_direction(section: ReportSection) -> tuple[int, int]:
    low_count = 0
    high_count = 0

    for param in section.parameters:
        sev = param.severity or ""
        if sev.startswith("low_"):
            low_count += 1
        elif sev.startswith("high_"):
            high_count += 1

    return low_count, high_count


def dominant_theme(section: ReportSection) -> str:
    low_count, high_count = count_direction(section)

    if low_count >= 2 and high_count == 0:
        return "depletion"
    if high_count >= 2 and low_count == 0:
        return "burden"
    if low_count >= 1 and high_count >= 1:
        return "mixed"
    if high_count > low_count:
        return "burden"
    if low_count > high_count:
        return "depletion"
    return "balanced"


def strongest_abnormality(section: ReportSection) -> str | None:
    abnormal_params = sort_abnormal_parameters(section)
    if not abnormal_params:
        return None
    return abnormal_params[0].source_name


def build_section_summary(section: ReportSection) -> str:
    title = section.display_title or section.source_title
    abnormal_count = section.abnormal_count
    priority = section.priority
    theme = dominant_theme(section)
    lead_marker = strongest_abnormality(section)

    if abnormal_count == 0:
        return f"{title} is broadly stable in this scan, with no notable deviations flagged by the analyser."

    if title == "Cardiovascular & circulation":
        if priority == "high":
            return (
                f"This section suggests a more meaningful cardiovascular strain pattern, with the strongest signal centred on {lead_marker}. "
                "The broader profile points more toward circulatory workload and altered flow dynamics than a single isolated variance."
            )
        if priority == "medium":
            return (
                "Circulatory markers show a meaningful deviation pattern, suggesting that vascular tone, perfusion, or cardiac efficiency may deserve closer review."
            )
        return (
            "There are some mild circulatory shifts here, but the pattern remains relatively contained rather than globally disrupted."
        )

    if title in {"Upper digestive function", "Lower digestive function"}:
        if theme == "depletion":
            return (
                "The digestive pattern leans more toward reduced efficiency than excess activity, particularly around motility, absorption, or digestive support."
            )
        if theme == "burden":
            return (
                "This section suggests a more activated or burdened digestive pattern, which may fit irritation, pressure, or functional overload rather than simple weakness."
            )
        return (
            "The digestive picture is mixed, which makes symptom correlation particularly important when interpreting the scan."
        )

    if title == "Liver & metabolic function":
        if lead_marker == "Liver Fat Content":
            return (
                "The liver pattern is relatively focused rather than diffuse, with fat-related metabolic load standing out more clearly than the other hepatic markers."
            )
        return (
            "This section suggests a modest hepatic or metabolic strain pattern rather than broad liver dysfunction across every marker."
        )

    if title == "Gallbladder & bile flow":
        return (
            "The pattern here is more consistent with mild disturbance in biliary handling or flow than with a broad collapse in function."
        )

    if title == "Trace elements":
        if theme == "depletion":
            return (
                "This trace element profile leans more toward insufficiency than overload, with the pattern suggesting reduced reserve across selected minerals."
            )
        if theme == "burden":
            return (
                "This section is less typical, with several markers tending upward rather than down, suggesting imbalance rather than straightforward deficiency."
            )
        return (
            "The trace element pattern is uneven rather than uniform, so interpretation is strongest when tied to symptoms and related nutrient markers."
        )

    if title == "Vitamin status":
        return (
            "This section suggests a broader micronutrient support issue rather than a single isolated vitamin abnormality, especially where multiple markers trend low together."
        )

    if title == "Heavy metal burden":
        return (
            "This section points toward an environmental burden pattern, with multiple toxic metal indicators contributing to the signal rather than one isolated marker alone."
        )

    if title == "Thyroid-related markers":
        return (
            "The thyroid-related markers behave more like a clustered regulatory pattern than a single outlier result, which makes this section more meaningful in context."
        )

    if title == "Skin function":
        return (
            "Skin-related findings combine barrier, structural, and inflammatory-style signals, suggesting a multi-factor pattern rather than one single dominant theme."
        )

    if title == "Allergy-related indicators":
        return (
            "This section suggests a heightened reactivity pattern, with the weight of evidence coming from several sensitivity markers rather than one isolated trigger."
        )

    if title == "Coenzyme status":
        return (
            "The coenzyme picture points more toward reduced metabolic support and energy cofactors than toward an excess or overactive state."
        )

    if title == "Essential fatty acids":
        return (
            "This section is more suggestive of shortfall than inflammatory excess, with several fatty-acid-related markers reading softer than expected."
        )

    if title == "Kidney & fluid balance":
        return (
            "The renal pattern here is uneven rather than uniform, suggesting some stress signals without a complete breakdown across all kidney-related markers."
        )

    if title == "Bone density & structure":
        return (
            "This section suggests structural vulnerability and remodelling pressure more than outright collapse, making it relevant to longer-term support planning."
        )

    if priority == "high":
        return (
            f"{title} is one of the more active sections in this scan, with multiple flagged markers combining into a broader functional pattern."
        )

    if priority == "medium":
        return (
            f"{title} contains a meaningful cluster of non-optimal markers, enough to warrant interpretation alongside symptoms, history, and related sections."
        )

    return (
        f"{title} shows a limited number of mild shifts, but not enough on their own to suggest a dominant imbalance."
    )


def build_overall_summary(report: ParsedReport) -> str:
    high_sections = [s.display_title or s.source_title for s in report.sections if s.priority == "high"]
    medium_sections = [s.display_title or s.source_title for s in report.sections if s.priority == "medium"]

    if high_sections:
        joined = ", ".join(high_sections[:4])
        return (
            f"The strongest themes in this scan sit around {joined}. "
            "Taken together, these findings suggest functional patterns worth discussing in follow-up rather than stand-alone diagnostic conclusions."
        )

    if medium_sections:
        joined = ", ".join(medium_sections[:4])
        return (
            f"The scan highlights a few areas worth exploring further, particularly {joined}. "
            "These findings are best interpreted alongside symptoms, history, and wider clinical context."
        )

    return (
        "This scan appears comparatively balanced overall, with most sections remaining within range and only limited areas drawing attention."
    )


def assign_marker_priority(param: ParameterResult) -> str:
    severity_score = SEVERITY_SCORES.get(param.severity or "unknown", 0)

    if severity_score >= 2:
        return "high"
    if severity_score == 1:
        return "medium"
    return "normal"


def apply_marker_definition(
    section: ReportSection,
    marker: ParameterResult,
    definition_index: dict,
) -> None:
    definition = get_marker_definition(
        definition_index,
        section.display_title or section.source_title,
        marker.source_name,
    )

    if not definition:
        return

    marker.what_it_means = definition.get("what_it_means") or marker.what_it_means
    marker.why_it_matters = definition.get("why_it_matters") or marker.why_it_matters
    marker.functional_significance = (
        definition.get("functional_significance") or marker.functional_significance
    )
    marker.common_patterns = definition.get("common_patterns") or marker.common_patterns

    clinical_relevance = definition.get("clinical_relevance")
    if clinical_relevance:
        marker.recommendation_notes = clinical_relevance

    severity = marker.severity or ""
    if severity.startswith("low"):
        marker.patient_interpretation = (
            definition.get("interpretation_low") or marker.patient_interpretation
        )
    elif severity.startswith("high"):
        marker.patient_interpretation = (
            definition.get("interpretation_high") or marker.patient_interpretation
        )

    if not marker.marker_priority or marker.marker_priority == "normal":
        marker.marker_priority = assign_marker_priority(marker)


def apply_insight_engine(report: ParsedReport) -> ParsedReport:
    definition_index = load_marker_definition_index()

    for section in report.sections:
        for marker in section.parameters:
            apply_marker_definition(section, marker, definition_index)

            if marker.is_abnormal is None:
                marker.is_abnormal = (marker.severity or "normal") != "normal"

            if not marker.marker_priority:
                marker.marker_priority = assign_marker_priority(marker)

        section.abnormal_count = sum(1 for p in section.parameters if p.is_abnormal)
        section.normal_count = sum(1 for p in section.parameters if not p.is_abnormal)
        section.section_score = compute_section_score(section)
        section.priority = classify_priority(section.section_score, section.abnormal_count)
        section.top_findings = build_top_findings(section)
        section.summary = build_section_summary(section)

    sorted_sections = sorted(
        report.sections,
        key=lambda s: (s.section_score, s.abnormal_count),
        reverse=True,
    )

    report.priority_sections = [
        s.display_title or s.source_title
        for s in sorted_sections[:5]
        if s.priority != "normal"
    ]

    report.overall_summary = build_overall_summary(report)
    return report