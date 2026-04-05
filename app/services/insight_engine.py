# app/services/insight_engine.py

from __future__ import annotations

from typing import Any

from app.models.schema import ParsedReport, ReportSection, ParameterResult
from app.services.marker_definition_service import build_definition_index, get_marker_definition


SECTION_FALLBACKS: dict[str, dict[str, str]] = {
    "cardiovascular & circulation": {
        "what_it_means": "This marker sits within the scan's broader picture of cardiovascular performance, circulation, perfusion, and vascular dynamics.",
        "why_it_matters": "Circulatory patterns can influence stamina, tissue delivery, vascular tone, and cardiac workload.",
        "functional_significance": "Changes here may suggest altered perfusion efficiency, vascular resistance, or broader circulatory strain.",
        "common_patterns": "This section becomes more meaningful when flow, resistance, and pump-related markers move together.",
    },
    "upper digestive function": {
        "what_it_means": "This marker contributes to the scan's picture of upper digestive function, including secretion, motility, and absorption.",
        "why_it_matters": "Digestive efficiency influences nutrient uptake, food tolerance, post-meal comfort, and wider metabolic resilience.",
        "functional_significance": "Changes here may suggest softer digestive efficiency, altered motility, or increased functional burden.",
        "common_patterns": "This section is often most useful alongside vitamin, amino acid, and trace element patterns.",
    },
    "lower digestive function": {
        "what_it_means": "This marker contributes to the scan's picture of lower digestive and bowel-related function.",
        "why_it_matters": "Lower digestive patterns may relate to motility, intestinal balance, elimination, and bowel comfort.",
        "functional_significance": "Changes here may suggest altered bowel motility, microbial imbalance, or pressure-related strain.",
        "common_patterns": "This section often becomes clearer when read with upper digestive, immune, and nutritional patterns.",
    },
    "liver & metabolic function": {
        "what_it_means": "This marker contributes to the scan's picture of liver-related metabolic, detoxification, and bile-associated function.",
        "why_it_matters": "Liver-linked patterns may influence metabolic efficiency, internal burden handling, lipid balance, and digestive support.",
        "functional_significance": "Changes here may suggest altered metabolic handling, increased burden, or reduced functional reserve.",
        "common_patterns": "This section is often stronger when read with gallbladder, obesity, blood sugar, and environmental burden findings.",
    },
    "gallbladder & bile flow": {
        "what_it_means": "This marker contributes to the scan's picture of gallbladder function and bile-related flow.",
        "why_it_matters": "Bile flow influences fat digestion, digestive rhythm, and wider hepatobiliary support.",
        "functional_significance": "Changes here may suggest altered bile handling, mild biliary strain, or inefficient digestive flow.",
        "common_patterns": "This section is often most useful when read with liver and upper digestive findings.",
    },
    "kidney & fluid balance": {
        "what_it_means": "This marker contributes to the scan's picture of kidney-related function and fluid balance.",
        "why_it_matters": "Kidney-linked patterns may relate to fluid handling, internal clearance, and biochemical balance.",
        "functional_significance": "Changes here may suggest altered fluid handling, clearance stress, or mild renal burden.",
        "common_patterns": "This section often becomes more meaningful when read with general resilience and body composition patterns.",
    },
    "lung function": {
        "what_it_means": "This marker contributes to the scan's picture of respiratory support and functional lung capacity.",
        "why_it_matters": "Lung-related patterns may influence oxygen handling, breath capacity, and exertional resilience.",
        "functional_significance": "Changes here may suggest softer respiratory reserve or altered functional breathing capacity.",
        "common_patterns": "This section is often read together with respiratory function, cardiovascular findings, and vitality patterns.",
    },
    "respiratory function": {
        "what_it_means": "This marker contributes to the scan's picture of respiratory support and oxygen-related resilience.",
        "why_it_matters": "Respiratory patterns may influence stamina, oxygenation, recovery, and wider vitality.",
        "functional_significance": "Changes here may suggest softer pulmonary support or reduced respiratory resilience.",
        "common_patterns": "This section is often read with lung, cardiovascular, and general vitality findings.",
    },
    "neurological function": {
        "what_it_means": "This marker contributes to the scan's picture of neurological and cognitive function.",
        "why_it_matters": "Neurological patterns can influence clarity, mood, coordination, and brain-related resilience.",
        "functional_significance": "Changes here may suggest softer neurocognitive support, altered signalling, or reduced reserve.",
        "common_patterns": "This section becomes stronger when read alongside circulation, endocrine, and general resilience findings.",
    },
    "bone density & structure": {
        "what_it_means": "This marker contributes to the scan's picture of bone density and structural support.",
        "why_it_matters": "Bone-related markers may reflect mineral support, structural resilience, and remodelling pressure.",
        "functional_significance": "Changes here may suggest reduced structural reserve, altered mineral support, or remodelling strain.",
        "common_patterns": "This section often becomes more meaningful when read with vitamin, trace element, and endocrine findings.",
    },
    "trace elements": {
        "what_it_means": "This marker contributes to the scan's picture of trace mineral sufficiency and balance.",
        "why_it_matters": "Mineral patterns may influence energy, tissue repair, enzyme systems, and broader metabolic resilience.",
        "functional_significance": "Changes here may suggest mineral shortfall, imbalance, or altered nutrient reserve.",
        "common_patterns": "This section is especially meaningful when several minerals trend in the same direction.",
    },
    "vitamin status": {
        "what_it_means": "This marker contributes to the scan's picture of vitamin sufficiency and micronutrient support.",
        "why_it_matters": "Vitamin patterns can influence energy production, antioxidant defence, immune support, and tissue repair.",
        "functional_significance": "Changes here may suggest reduced micronutrient reserve or broader nutritional softness.",
        "common_patterns": "This section is especially meaningful when multiple vitamin markers trend low together.",
    },
    "amino acids": {
        "what_it_means": "This marker contributes to the scan's picture of amino acid support and protein-related metabolic function.",
        "why_it_matters": "Amino acid patterns may influence repair, neurotransmitter balance, detoxification, and metabolic resilience.",
        "functional_significance": "Changes here may suggest softer protein support or reduced amino acid reserve.",
        "common_patterns": "This section often becomes more useful when read with digestive, vitamin, and coenzyme findings.",
    },
    "coenzyme status": {
        "what_it_means": "This marker contributes to the scan's picture of cofactor and coenzyme support in metabolism.",
        "why_it_matters": "Coenzyme-related patterns may influence energy production, mitochondrial function, and biochemical efficiency.",
        "functional_significance": "Changes here may suggest reduced metabolic support or weaker biochemical reserve.",
        "common_patterns": "This section often becomes stronger when read with vitamin and amino-acid patterns.",
    },
    "essential fatty acids": {
        "what_it_means": "This marker contributes to the scan's picture of essential fatty acid status and membrane support.",
        "why_it_matters": "Essential fatty acids can influence inflammation balance, tissue resilience, circulation, and membrane function.",
        "functional_significance": "Changes here may suggest a shortfall pattern or reduced fatty-acid support.",
        "common_patterns": "This section often links with cardiovascular, skin, endocrine, and vitality-related patterns.",
    },
    "endocrine system": {
        "what_it_means": "This marker contributes to the scan's picture of hormonal and glandular regulation.",
        "why_it_matters": "Endocrine patterns may influence energy, stress response, metabolic tempo, recovery, and long-term resilience.",
        "functional_significance": "Changes here may suggest softer glandular support or altered regulatory balance.",
        "common_patterns": "This section often becomes more useful when read with thyroid, vitality, and weight-related findings.",
    },
    "immune system": {
        "what_it_means": "This marker contributes to the scan's picture of immune balance and defensive resilience.",
        "why_it_matters": "Immune patterns may influence resilience, reactivity, recovery, and barrier defence.",
        "functional_significance": "Changes here may suggest altered immune readiness or softer defensive reserve.",
        "common_patterns": "This section often becomes more meaningful when read with allergy, digestive, and skin findings.",
    },
    "thyroid-related markers": {
        "what_it_means": "This marker contributes to the scan's picture of thyroid-related regulation and metabolic tempo.",
        "why_it_matters": "Thyroid-linked patterns may influence energy, temperature regulation, metabolic pace, and recovery.",
        "functional_significance": "Changes here may suggest altered metabolic signalling or reduced thyroid-related balance.",
        "common_patterns": "This section is often strongest when read with endocrine, weight, and general resilience findings.",
    },
    "environmental burden markers": {
        "what_it_means": "This marker contributes to the scan's picture of environmental or internal burden-related patterns.",
        "why_it_matters": "Burden-related patterns may influence resilience, detoxification demand, and tissue stress.",
        "functional_significance": "Changes here may suggest increased background load or reduced tolerance to internal stressors.",
        "common_patterns": "This section often becomes more meaningful when read with liver, kidney, and heavy metal findings.",
    },
    "heavy metal burden": {
        "what_it_means": "This marker contributes to the scan's picture of heavy metal burden patterns.",
        "why_it_matters": "Heavy-metal-style patterns may relate to resilience, detoxification demand, and biochemical stress.",
        "functional_significance": "Changes here may suggest increased burden or reduced tolerance in this area.",
        "common_patterns": "This section is often most meaningful when read with environmental burden, liver, and general resilience findings.",
    },
    "general physical resilience": {
        "what_it_means": "This marker contributes to the scan's picture of general physical resilience and baseline vitality.",
        "why_it_matters": "General resilience patterns can reflect reserve capacity, adaptation, and wider functional balance.",
        "functional_significance": "Changes here may suggest lower reserve, softer adaptation, or increased background strain.",
        "common_patterns": "This section often provides useful context for more specific body-system findings.",
    },
    "allergy-related indicators": {
        "what_it_means": "This marker contributes to the scan's picture of sensitivity and reactivity patterns.",
        "why_it_matters": "Reactivity markers may relate to histamine-like patterns, immune sensitivity, and environmental responsiveness.",
        "functional_significance": "Changes here may suggest heightened reactivity or stronger sensitivity burden.",
        "common_patterns": "This section becomes stronger when several triggers or reactivity indicators move together.",
    },
    "obesity and weight patterns": {
        "what_it_means": "This marker contributes to the scan's picture of weight regulation and metabolic pressure.",
        "why_it_matters": "Weight-related patterns may reflect lipid handling, metabolic efficiency, and wider resilience.",
        "functional_significance": "Changes here may suggest metabolic burden, reduced efficiency, or altered fat-regulation patterns.",
        "common_patterns": "This section is often most useful alongside blood sugar, liver, and cardiovascular findings.",
    },
    "skin function": {
        "what_it_means": "This marker contributes to the scan's picture of skin integrity, barrier support, and tissue balance.",
        "why_it_matters": "Skin-related patterns may reflect barrier health, inflammation tendency, structural support, or hydration patterns.",
        "functional_significance": "Changes here may suggest barrier weakness, inflammatory strain, or reduced tissue support.",
        "common_patterns": "This section often links with collagen, vitamins, essential fatty acids, and allergy-related findings.",
    },
}


def _normalise(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.lower().strip().split())


def _severity_rank(severity: str | None) -> int:
    mapping = {
        "high_severe": 6,
        "low_severe": 6,
        "high_moderate": 5,
        "low_moderate": 5,
        "high_mild": 4,
        "low_mild": 4,
        "normal": 1,
        "unknown": 0,
        None: 0,
    }
    return mapping.get(severity, 0)


def _marker_priority_from_severity(severity: str | None) -> str:
    if severity in {"high_severe", "low_severe", "high_moderate", "low_moderate"}:
        return "high"
    if severity in {"high_mild", "low_mild"}:
        return "medium"
    return "normal"


def _severity_phrase(severity: str | None) -> str:
    mapping = {
        "normal": "within range",
        "low_mild": "mildly reduced",
        "low_moderate": "moderately reduced",
        "low_severe": "markedly reduced",
        "high_mild": "mildly elevated",
        "high_moderate": "moderately elevated",
        "high_severe": "markedly elevated",
    }
    return mapping.get(severity, "shifted")


def _directional_patient_interpretation(severity: str | None) -> str:
    mapping = {
        "normal": "This marker sits within the analyser's expected range in this scan.",
        "low_mild": "A mildly reduced result may suggest softer functional support or reserve in this area.",
        "low_moderate": "A more clearly reduced result may point toward weaker functional capacity or suboptimal support in this area.",
        "low_severe": "A strongly reduced result may suggest a more pronounced shortfall or reduced reserve in this area.",
        "high_mild": "A mildly elevated result may suggest early strain, burden, or compensation in this area.",
        "high_moderate": "A more clearly elevated result may support a stronger pattern of stress, overload, or imbalance in this area.",
        "high_severe": "A markedly elevated result may suggest a more pronounced functional burden or imbalance in this area.",
    }
    return mapping.get(severity, "This marker shows a shift worth interpreting in the context of the wider pattern.")


def _section_fallback(section_title: str | None) -> dict[str, str]:
    return SECTION_FALLBACKS.get(_normalise(section_title), {})


def _clean_sentence(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text if text else None


def _pick(*values: str | None) -> str | None:
    for value in values:
        cleaned = _clean_sentence(value)
        if cleaned:
            return cleaned
    return None


def enrich_marker(
    section: ReportSection,
    marker: ParameterResult,
    definition_index: dict[tuple[str, str], dict[str, Any]],
) -> None:
    definition = get_marker_definition(
        definition_index,
        section.display_title or section.source_title,
        marker.source_name,
    )
    fallback = _section_fallback(section.display_title or section.source_title)

    marker.what_it_means = _pick(
        definition.get("clinical_meaning"),
        fallback.get("what_it_means"),
        f"This marker contributes to the scan's picture of {((section.display_title or section.source_title or 'this section').lower())}.",
    )

    marker.why_it_matters = _pick(
        definition.get("why_it_matters"),
        fallback.get("why_it_matters"),
    )

    marker.functional_significance = _pick(
        definition.get("functional_significance"),
        fallback.get("functional_significance"),
    )

    marker.common_patterns = _pick(
        definition.get("pattern_links"),
        fallback.get("common_patterns"),
    )

    marker.patient_interpretation = _pick(
        definition.get("low_interpretation") if "low" in (marker.severity or "") else None,
        definition.get("high_interpretation") if "high" in (marker.severity or "") else None,
        _directional_patient_interpretation(marker.severity),
    )

    marker.marker_priority = _pick(
        definition.get("marker_priority"),
        _marker_priority_from_severity(marker.severity),
    )

def build_top_findings(section: ReportSection, max_items: int = 3) -> list[str]:
    abnormal = [p for p in section.parameters if p.is_abnormal]
    abnormal = sorted(abnormal, key=lambda p: (_severity_rank(p.severity), p.source_name.lower()), reverse=True)

    findings: list[str] = []
    for marker in abnormal[:max_items]:
        findings.append(f"{marker.source_name} is {_severity_phrase(marker.severity)}")
    return findings


def build_section_summary(section: ReportSection) -> str:
    title = section.display_title or section.source_title or "This section"

    if section.abnormal_count == 0:
        return f"{title} appears broadly steady in this scan, without a notable cluster of flagged markers."

    if section.section_score >= 7:
        return f"The strongest signal in {title.lower()} comes from multiple markers moving together, suggesting a broader functional pattern rather than a single isolated variance."

    if section.section_score >= 5:
        return f"{title} shows a meaningful cluster of shifted markers, enough to warrant interpretation alongside symptoms, history, and related sections."

    if section.section_score >= 3:
        return f"{title} shows a smaller but still relevant pattern of shifted markers that may be worth following up in context."

    return f"{title} shows a limited number of mild shifts, best interpreted alongside the wider scan picture."


def compute_section_priority(section: ReportSection) -> str:
    if section.section_score >= 6 or section.abnormal_count >= 5:
        return "high"
    if section.section_score >= 2 or section.abnormal_count >= 2:
        return "medium"
    if section.abnormal_count >= 1:
        return "low"
    return "normal"


def compute_section_counts_and_score(section: ReportSection) -> None:
    abnormal_count = 0
    normal_count = 0
    score = 0

    for marker in section.parameters:
        if marker.is_abnormal:
            abnormal_count += 1
            if marker.severity in {"high_severe", "low_severe"}:
                score += 3
            elif marker.severity in {"high_moderate", "low_moderate"}:
                score += 2
            elif marker.severity in {"high_mild", "low_mild"}:
                score += 1
        else:
            normal_count += 1

    section.abnormal_count = abnormal_count
    section.normal_count = normal_count
    section.section_score = score
    section.priority = compute_section_priority(section)


def build_overall_summary(report: ParsedReport) -> str:
    priority_sections = [
        s.display_title or s.source_title
        for s in sorted(report.sections, key=lambda s: (s.section_score, s.abnormal_count), reverse=True)
        if s.priority in {"high", "medium"} and s.abnormal_count > 0
    ]

    top = priority_sections[:4]
    if not top:
        return "The scan does not show a dominant cluster of flagged markers. Findings are best interpreted in the context of symptoms, history, and wider clinical judgement."

    joined = ", ".join(top[:-1]) + (f", {top[-1]}" if len(top) > 1 else top[0])
    return f"The strongest themes in this scan sit around {joined}. Taken together, these findings suggest functional patterns worth discussing in follow-up rather than stand-alone diagnostic conclusions."


def enrich_report_with_marker_intelligence(report: ParsedReport) -> ParsedReport:
    definition_index = build_definition_index()

    for section in report.sections:
        for marker in section.parameters:
            enrich_marker(section, marker, definition_index)

        compute_section_counts_and_score(section)
        section.summary = build_section_summary(section)
        section.top_findings = build_top_findings(section)

    report.sections = sorted(report.sections, key=lambda s: (s.section_score, s.abnormal_count), reverse=True)
    report.priority_sections = [
        s.display_title or s.source_title
        for s in report.sections
        if s.priority in {"high", "medium"} and s.abnormal_count > 0
    ][:6]
    report.overall_summary = build_overall_summary(report)

    return report