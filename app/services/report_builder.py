from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.models.schema import ParsedReport, ReportOverrides, ParameterResult
from app.services.config_service import load_practitioner_config
from app.services.pdf_service import save_html, save_pdf
from app.services.marker_definition_service import load_marker_definition_index, get_marker_definition
from app.services.scoring_engine import compute_scan_scores
from app.services.pattern_engine_v2 import attach_pattern_engine_v2_output
from app.services.product_resolver import resolve_all_products
from app.services.protocol_composer import compose_protocol
from app.services.ai_narrative_engine_v2 import enrich_protocol_plan_with_narrative
from app.services.product_mapping_builder import build_complete_product_mapping
from datetime import datetime
from zoneinfo import ZoneInfo
import os
from app.services.ai_narrative_engine_v3 import (
    enrich_protocol_plan_with_narrative_v3,
    rewrite_clinical_recommendations_v3,
    rewrite_at_a_glance_v3,
)
from app.config.product_recommendation_settings import (
    normalize_recommendation_mode,
    products_enabled,
)



ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "app" / "templates"

NON_DISPLAY_SECTIONS = {"expert analysis", "hand analysis"}
EXCLUDED_PRIORITY_SECTIONS = {"element of human", "basic physical quality"}


def is_hidden_section(title: str | None) -> bool:
    return (title or "").strip().lower() in NON_DISPLAY_SECTIONS


NEUTRAL_TERMINOLOGY_MAP = {
    "adolescent intelligence": "Cognitive function",
    "adolescent growth index": "Growth and development",
    "adolescent cognitive development": "Cognitive function",
    "adolescent growth": "Growth and development",
}


RECOMMENDATION_TITLE_MAP = {
    "eye": "Visual and microvascular support",
    "obesity": "Metabolic regulation and weight balance",
    "collagen": "Connective tissue and structural support",
    "channels and collaterals": "Circulatory and microvascular support",
    "lecithin": "Cell membrane integrity and lipid transport",
    "fatty acid": "Essential fatty acid and membrane balance",
    "amino acid": "Protein metabolism and neurotransmitter support",
    "trace element": "Mineral balance and cofactor support",
    "vitamin": "Vitamin sufficiency and antioxidant support",
}

RATIONALE_SECTION_LABEL_MAP = {
    "Adolescent Intelligence": "Cognitive function",
    "Adolescent Growth Index": "Growth and development",
    "Lecithin": "Cell membrane integrity and lipid transport",
    "Channels and collaterals": "Circulatory and microvascular pathways",
    "Eye": "Visual and microvascular support",
    "Obesity": "Metabolic regulation and weight balance",
    "Collagen": "Connective tissue and structural support",
}

RECOMMENDATION_FAMILY_MAP = {
    "Nutrient repletion": "nutrient_repletion",
    "Inflammation and barrier support": "barrier_inflammation",
    "Cardiovascular support": "cardiovascular",
    "Circulatory and microvascular support": "circulatory_microvascular",
    "Visual and microvascular support": "circulatory_microvascular",
    "Metabolic regulation and weight balance": "metabolic",
    "Connective tissue and structural support": "connective_tissue",
    "Cognitive function": "neurocognitive",
    "Growth and development": "growth_development",
    "Cell membrane integrity and lipid transport": "lipid_membrane",
    
}


PATTERN_RECOMMENDATION_BOOSTS = {
    "absorption_assimilation": {
        "nutrient_repletion": 3.0,
        "barrier_inflammation": 1.0,
        "lipid_membrane": 0.5,
        "neurocognitive": 0.5,
    },
    "toxic_burden": {
        "barrier_inflammation": 1.5,
        "nutrient_repletion": 1.0,
        "circulatory_microvascular": 0.5,
    },
    "inflammatory_barrier": {
        "barrier_inflammation": 3.0,
        "nutrient_repletion": 0.75,
        "lipid_membrane": 1.0,
    },
    "neurocognitive_support": {
        "neurocognitive": 3.0,
        "nutrient_repletion": 1.0,
        "lipid_membrane": 1.5,
        "growth_development": 1.0,
    },
    "mitochondrial_energy": {
        "nutrient_repletion": 2.0,
        "metabolic": 1.5,
        "neurocognitive": 0.5,
    },
    "glycaemic_metabolic": {
        "metabolic": 4.0,
        "cardiovascular": 1.5,
        "nutrient_repletion": 1.0,
    },
    "lipid_transport_membrane": {
        "lipid_membrane": 3.0,
        "neurocognitive": 1.0,
        "barrier_inflammation": 1.0,
    },
    "connective_tissue_repair": {
        "connective_tissue": 3.0,
        "nutrient_repletion": 1.0,
        "barrier_inflammation": 0.5,
    },
}

def normalise_rationale_leading_section(text: str | None) -> str | None:
    if not text:
        return text

    out = text
    for old, new in RATIONALE_SECTION_LABEL_MAP.items():
        prefix = f"{old} contains "
        replacement = f"{new} contains "
        out = out.replace(prefix, replacement)

    return out


RECOMMENDATION_NARRATIVE_MAP = {
    "eye": "visual and microvascular function",
    "obesity": "metabolic balance and weight regulation",
    "collagen": "connective tissue integrity",
    "channels and collaterals": "circulatory and microvascular pathways",
    "lecithin": "cell membrane integrity and lipid transport",
}




def normalise_display_term(term: str | None) -> str:
    if not term:
        return ""
    key = term.strip().lower()
    return NEUTRAL_TERMINOLOGY_MAP.get(key, term)


def normalise_recommendation_title(title: str | None) -> str:
    if not title:
        return ""
    key = title.strip().lower()
    return RECOMMENDATION_TITLE_MAP.get(key, title)


def normalise_narrative_text(text: str | None) -> str | None:
    if not text:
        return text

    replacements = {
        # Child terminology cleanup
        "Adolescent Intelligence": "Cognitive function",
        "Adolescent Growth Index": "Growth and development",
        "adolescent intelligence": "cognitive function",
        "adolescent growth index": "growth and development",
        "adolescent growth": "growth and development",
        "adolescent cognitive development": "cognitive function",
        "Growth indices": "Growth indicators",
        "cognitive function and growth indices": "cognitive function and growth patterns",
        "Cognitive function and growth indices": "Cognitive function and growth patterns",
        "growth indices": "growth indicators",
        

        # Compound child phrases
        "Adolescent intelligence markers": "Cognitive function markers",
        "adolescent intelligence markers": "cognitive function markers",
        "Adolescent intelligence and growth indices": "Cognitive function and growth indicators",
        "adolescent intelligence and growth indices": "cognitive function and growth indicators",
        "Growth index abnormalities": "Growth and development indicators",
        "growth index abnormalities": "growth and development indicators",
        "Growth-and-development": "Growth and development",
        "growth-and-development": "growth and development",
        "Adolescent intelligence": "Cognitive development",
        "adolescent intelligence": "cognitive development",
        "Adolescent intelligence and growth indicators": "Cognitive development and growth indicators",
        "adolescent intelligence and growth indicators": "cognitive development and growth indicators",

        # Neutral person language
        "this adolescent": "the individual",
        "the adolescent": "the individual",
        "this child": "the individual",
        "the child": "the individual",

        # Clinic languagefor recommendation narratives
        "channels and collaterals": "circulatory and microvascular pathways",
        "Channels and collaterals": "Circulatory and microvascular pathways",
        "Lecithin markers": "Cell membrane integrity and lipid transport markers",
        "lecithin markers": "cell membrane integrity and lipid transport markers",
        "lecithin-related markers": "cell membrane and lipid-transport markers",
        "Lecithin-related markers": "Cell membrane and lipid-transport markers",
        "lecithin levels": "cell membrane and lipid-transport status",
        "Lecithin levels": "Cell membrane and lipid-transport status",
        "lecithin patterns": "cell membrane and lipid-transport patterns",
        "Lecithin patterns": "Cell membrane and lipid-transport patterns",
        "lecithin-related patterns": "cell membrane and lipid-transport patterns",
        "Lecithin-related patterns": "Cell membrane and lipid-transport patterns",
        "lecithin-related nutrient status": "cell membrane and lipid-transport status",
        "Lecithin-related nutrient status": "Cell membrane and lipid-transport status",

        "cognitive development patterns": "cognitive development",
        "Cognitive development patterns": "Cognitive development",

        # Male-centric terminology cleanup
        "obesity-related markers": "metabolic-balance and weight-regulation markers",
        "Obesity-related markers": "Metabolic-balance and weight-regulation markers",
        "obesity patterns": "metabolic balance and weight regulation",
        "Obesity patterns": "Metabolic balance and weight regulation",
        "collagen support": "connective tissue support",
        "Collagen support": "Connective tissue support",
    



    }

    out = text
    for old, new in replacements.items():
        out = out.replace(old, new)

    return out


def normalise_recommendation_narrative(text: str | None) -> str | None:
    if not text:
        return text

    out = text

    replacements = {
        "Provide targeted support for circulatory and microvascular pathways patterns": "Provide targeted support for circulatory and microvascular pathways",

        "Provide targeted support for channels and collaterals patterns": "Provide targeted support for circulatory and microvascular pathways",
        "Provide targeted support for Channels and collaterals patterns": "Provide targeted support for circulatory and microvascular pathways",

        "Provide targeted support for eye patterns": "Provide targeted support for visual and microvascular function",
        "Provide targeted support for Eye patterns": "Provide targeted support for visual and microvascular function",

        "Provide targeted support for obesity patterns": "Provide targeted support for metabolic balance and weight regulation",
        "Provide targeted support for Obesity patterns": "Provide targeted support for metabolic balance and weight regulation",

        "Provide targeted support for collagen patterns": "Provide targeted support for connective tissue integrity",
        "Provide targeted support for Collagen patterns": "Provide targeted support for connective tissue integrity",

        "Provide targeted support for lecithin patterns": "Provide targeted support for cell membrane integrity and lipid transport",
        "Provide targeted support for Lecithin patterns": "Provide targeted support for cell membrane integrity and lipid transport",

        "cognitive function patterns": "cognitive function",
        "Cognitive function patterns": "Cognitive function",

        "growth and development patterns": "growth and development",
        "Growth and development patterns": "Growth and development",

        "cell membrane integrity and lipid transport patterns": "cell membrane integrity and lipid transport",
        "Cell membrane integrity and lipid transport patterns": "Cell membrane integrity and lipid transport",
        "cell membrane and lipid-transport patterns": "cell membrane and lipid transport",
        "Cell membrane and lipid-transport patterns": "Cell membrane and lipid transport",


    }

    for old, new in replacements.items():
        out = out.replace(old, new)

    return out


def rank_v2_clinical_recommendations(report) -> list[dict]:
    raw_recs = getattr(report, "clinical_recommendations", []) or []
    if not raw_recs:
        return []

    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    pattern_keys = []
    if primary:
        pattern_keys.append(primary.key)
    pattern_keys.extend([p.key for p in patterns[1:4]])

    ranked = []

    for rec in raw_recs:
        title = rec.get("title", "")
        family = RECOMMENDATION_FAMILY_MAP.get(title, title.lower().replace(" ", "_"))

        score = 1.0

        rationale = rec.get("rationale", "") or ""
        summary = rec.get("summary", "") or ""

        # crude evidence strength from flagged marker counts in rationale
        import re
        m = re.search(r"contains (\d+) flagged markers", rationale)
        if m:
            score += min(int(m.group(1)) * 0.2, 2.0)

        # primary / contributing pattern boosts
        for idx, key in enumerate(pattern_keys):
            boosts = PATTERN_RECOMMENDATION_BOOSTS.get(key, {})
            base_boost = boosts.get(family, 0.0)
            if idx == 0:
                score += base_boost
            else:
                score += base_boost * 0.6

        # slightly boost if recommendation summary sounds broad / foundational
        if "foundational" in summary.lower():
            score += 0.3


        primary_key = primary.key if primary else None

        if primary_key:
            primary_boosts = PATTERN_RECOMMENDATION_BOOSTS.get(primary_key, {})
            if family not in primary_boosts:
                score -= 0.5
            

        ranked.append({
            **rec,
            "_family": family,
            "_score": round(score, 3),
        })

    # keep best recommendation per family
    best_by_family = {}
    for rec in ranked:
        family = rec["_family"]
        if family not in best_by_family or rec["_score"] > best_by_family[family]["_score"]:
            best_by_family[family] = rec

    deduped = list(best_by_family.values())
    deduped.sort(key=lambda x: x["_score"], reverse=True)

    return deduped[:6]


def priority_label(priority: str | None) -> str:
    return {
        "high": "High priority",
        "medium": "Medium priority",
        "low": "Low priority",
        "normal": "Within range",
    }.get(priority or "normal", "Within range")



def polished_overall_band_label(score: float | int | None) -> str:
    if score is None:
        return "Unclassified"

    score = float(score)

    if score >= 85:
        return "Robust functional balance"
    if score >= 70:
        return "Generally balanced"
    if score >= 55:
        return "Needs focused support"
    return "Priority review recommended"


def severity_display(severity: str | None) -> str:
    mapping = {
        "normal": "Within range",
        "low_mild": "Mildly reduced",
        "low_moderate": "Moderately reduced",
        "low_severe": "Markedly reduced",
        "high_mild": "Mildly elevated",
        "high_moderate": "Moderately elevated",
        "high_severe": "Markedly elevated",
        "unknown": "Unclear",
    }
    return mapping.get(severity or "unknown", "Unclear")


def severity_class(severity: str | None) -> str:
    if severity in {"high_severe", "low_severe"}:
        return "sev-severe"
    if severity in {"high_moderate", "low_moderate"}:
        return "sev-moderate"
    if severity in {"high_mild", "low_mild"}:
        return "sev-mild"
    return "sev-normal"


def status_pointer_position(severity: str | None, variant: str = "standard") -> str:
    """
    7-band QRMA-style layout:
    red | yellow | blue | green | blue | yellow | red

    Reduced values sit LEFT of green.
    Elevated values sit RIGHT of green.
    """
    mapping = {
        "low_severe": "8%",
        "low_moderate": "20%",
        "low_mild": "34%",
        "normal": "50%",
        "high_mild": "66%",
        "high_moderate": "80%",
        "high_severe": "92%",
        "unknown": "50%",
    }
    return mapping.get(severity or "unknown", "50%")



def status_bar_variant_for_section(section_title: str | None) -> str:
    return "lung" if (section_title or "").strip().lower() == "lung function" else "standard"


def is_body_composition_section(section_title: str | None) -> bool:
    return (section_title or "").strip().lower() in {"body composition analysis", "element of human"}


def dedupe_sections_by_title(sections):
    seen, deduped = set(), []
    for section in sections:
        title = (section.display_title or section.source_title or "").strip().lower()
        if not title or is_hidden_section(title):
            continue
        if title not in seen:
            seen.add(title)
            deduped.append(section)
    return deduped


def _section_card_map(report: ParsedReport):
    all_non_body = [
        s for s in dedupe_sections_by_title(report.sections)
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
    ]
    scan_scores = compute_scan_scores(all_non_body)

    # 🔥 ADD THIS LINE (Pattern Engine V2)
    report = attach_pattern_engine_v2_output(report)

  

    return {
        (card["title"] or "").strip().lower(): card
        for card in scan_scores.get("section_score_cards", [])
    }


def _derived_priority(section_score: float) -> str:
    if section_score <= 55:
        return "high"
    if section_score <= 70:
        return "medium"
    if section_score <= 85:
        return "low"
    return "normal"


def select_report_sections(report: ParsedReport, max_sections: int = 6):
    deduped = [
        s for s in dedupe_sections_by_title(report.sections)
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
        and (s.display_title or s.source_title or "").strip().lower() not in EXCLUDED_PRIORITY_SECTIONS
    ]

    card_map = _section_card_map(report)

    def _score_key(section):
        title = (section.display_title or section.source_title or "").strip().lower()
        card = card_map.get(title, {})
        return card.get("score", 100)

    return sorted(deduped, key=_score_key)[:max_sections]


def select_priority_marker_cards(section, max_markers: int = 4):
    candidates = [p for p in section.parameters if p.severity in {"high_moderate", "low_moderate", "high_severe", "low_severe"}]
    weights = {"high_severe": 6, "low_severe": 6, "high_moderate": 5, "low_moderate": 5}
    return sorted(candidates, key=lambda p: (weights.get(p.severity or "unknown", 0), p.source_name.lower()), reverse=True)[:max_markers]


def build_priority_overview(report):
    filtered_sections = [
        s for s in dedupe_sections_by_title(report.sections)
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
        and (s.display_title or s.source_title or "").strip().lower() not in EXCLUDED_PRIORITY_SECTIONS
    ]

    section_card_map = _section_card_map(report)

    def _score_key(section):
        section_title = (section.display_title or section.source_title or "").strip().lower()
        card = section_card_map.get(section_title, {})
        return card.get("score", 100)

    sorted_sections = sorted(filtered_sections, key=_score_key)[:6]

    out = []
    for section in sorted_sections:
        section_title = section.display_title or section.source_title
        card = section_card_map.get(section_title.lower(), {})

        total_parameters = len(section.parameters or [])
        section_score = card.get("score", section.section_score)
        flagged_count = card.get("flagged_count", section.abnormal_count)
        within_count = card.get("within_count", max(0, total_parameters - section.abnormal_count))
        priority = _derived_priority(section_score)

        out.append({
            "title": normalise_display_term(section_title),
            "priority": priority,
            "priority_label": priority_label(priority),
            "abnormal_count": flagged_count,
            "section_score": section_score,
            "flagged_count": flagged_count,
            "within_count": within_count,
            "total_count": total_parameters,
        })

    return out


def build_priority_sections_list(report: ParsedReport) -> list[str]:
    filtered = [
        s for s in dedupe_sections_by_title(report.sections)
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
        and (s.display_title or s.source_title or "").strip().lower() not in EXCLUDED_PRIORITY_SECTIONS
    ]

    card_map = _section_card_map(report)

    def _score_key(section):
        title = (section.display_title or section.source_title or "").strip().lower()
        card = card_map.get(title, {})
        return card.get("score", 100)

    sorted_sections = sorted(filtered, key=_score_key)

    return [
        normalise_display_term(s.display_title or s.source_title)
        for s in sorted_sections
        if card_map.get((s.display_title or s.source_title or "").strip().lower(), {}).get("flagged_count", 0) > 0
    ][:6]



def build_priority_category_review_intro(priority_sections: list[str]) -> dict | None:
    if not priority_sections:
        return None

    return {
        "title": "Priority category review",
        "summary": (
            "This section presents the most relevant functional categories identified in the scan. "
            "These categories have been prioritised based on the number, severity, and clustering of "
            "non-optimal markers.\n\n"
            "Each category page summarises the strongest findings in that category and highlights the "
            "most clinically relevant marker explanations to support efficient interpretation and follow-up planning."
        ),
        "top_sections": priority_sections[:6],
    }



def body_comp_display_name(source_name: str) -> str:
    raw = (source_name or "").strip()
    key = raw.lower()

    mapping = {
        "1.the componential analysis of body: (1)intracellular fluid (l)": "Intracellular fluid (QRMA)",
        "1.the componential analysis of body: (2) extracellular fluid(l)": "Extracellular fluid (QRMA)",
        "1.the componential analysis of body: (3)protein(kg)": "Protein mass (QRMA)",
        "1.the componential analysis of body: (4)inorganic substance(kg)": "Mineral mass (QRMA)",
        "1.the componential analysis of body: (5)body fat (kg)": "Body fat mass (QRMA)",
        "2.fat analysis: 1.height(cm)": "Height",
        "2.fat analysis: 2.weight(kg)": "Body weight",
        "2.fat analysis: 3.muscle mass": "Muscle mass / muscle volume",
        "2.fat analysis: 4.body fat content": "Body fat mass",
        "2.fat analysis: 5.body fat percentage": "Body fat percentage",
        "2.fat analysis: 6.ratio of abdominal fat": "Abdominal fat ratio",
        "obesity degree of body(odb)": "Obesity degree of body (QRMA)",
        "body mass index (bmi)": "Body mass index (BMI)",
        "basal metabolism rate(bmr)": "Basal metabolic rate (BMR)",
        "body cell mass (bcm)": "Body cell mass (BCM)",
        "target weight": "Target weight",
        "weight control": "Weight change target",
        "fat control": "Fat mass change target",
        "muscle control": "Muscle mass change target",
        "body form assessment": "Body shape / form assessment",
    }

    return mapping.get(key, raw)


def classify_body_comp_marker(name: str) -> str:
    n = (name or "").strip().lower()

    if any(term in n for term in [
        "intracellular fluid",
        "extracellular fluid",
        "body water",
        "body moisture",
        "fluid",
        "hydration",
    ]):
        return "Fluid & Hydration"

    if any(term in n for term in [
        "protein mass",
        "mineral mass",
        "muscle",
        "lean body",
        "body cell mass",
    ]):
        return "Lean Tissue & Structural Mass"

    if any(term in n for term in [
        "body fat mass",
        "body fat percentage",
        "abdominal fat",
        "fat mass",
        "adiposity",
    ]):
        return "Adiposity & Fat Distribution"

    if any(term in n for term in [
        "body weight",
        "height",
        "body mass index",
        "target weight",
        "weight change target",
        "fat mass change target",
        "muscle mass change target",
        "basal metabolic rate",
        "obesity degree",
    ]):
        return "Weight & Metabolic Indicators"

    return "Other QRMA Body Composition Indices"


def _overlay_definition(section_title: str, marker: ParameterResult) -> ParameterResult:
    try:
        index = load_marker_definition_index()
        system = getattr(marker, "original_report_category", None) or section_title

        row = None
        lookup_candidates = []

        if getattr(marker, "display_label", None):
            lookup_candidates.append(marker.display_label)
        if getattr(marker, "clinical_label", None):
            lookup_candidates.append(marker.clinical_label)
        if getattr(marker, "source_name", None):
            lookup_candidates.append(marker.source_name)

        for candidate in lookup_candidates:
            row = get_marker_definition(index, system, candidate)
            if row:
                break

        if not row:
            return marker

        marker.control_id = row.get("control_id") or getattr(marker, "control_id", None)
        marker.canonical_system = row.get("canonical_system") or getattr(marker, "canonical_system", None)
        marker.clinical_label = row.get("clinical_label") or getattr(marker, "clinical_label", None)
        marker.original_report_category = row.get("original_report_category") or getattr(marker, "original_report_category", None)
        marker.display_label = marker.clinical_label or getattr(marker, "display_label", None) or marker.source_name
        marker.pattern_cluster = row.get("pattern_cluster") or getattr(marker, "pattern_cluster", None)
        marker.what_it_means = row.get("clinical_meaning") or getattr(marker, "what_it_means", None)
        marker.why_it_matters = row.get("why_it_matters") or getattr(marker, "why_it_matters", None)
        marker.functional_significance = row.get("functional_significance") or getattr(marker, "functional_significance", None)
        marker.common_patterns = row.get("pattern_links") or getattr(marker, "common_patterns", None)
        marker.recommendation_notes = row.get("recommendation_hint") or getattr(marker, "recommendation_notes", None)

        status_text = severity_display(getattr(marker, "severity", None)).lower()
        if "reduced" in status_text or "low" in status_text:
            marker.patient_interpretation = row.get("low_interpretation") or getattr(marker, "patient_interpretation", None)
        elif "elevated" in status_text or "high" in status_text:
            marker.patient_interpretation = row.get("high_interpretation") or getattr(marker, "patient_interpretation", None)
        else:
            label = marker.display_label or marker.source_name
            marker.patient_interpretation = f"{label} appears within the analyser's expected range in this scan."
    except Exception:
        return marker
    return marker


def _meaning_text(marker: ParameterResult) -> str:
    return marker.what_it_means or marker.functional_significance or marker.why_it_matters or ""


def _extract_numeric(value_text: str | None) -> float | None:
    if not value_text:
        return None
    import re
    match = re.search(r"-?\d+(?:\.\d+)?", str(value_text))
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _normalise_body_comp_value(label: str, value_text: str | None) -> str:
    if not value_text:
        return ""

    value = str(value_text).strip()

    if label == "Abdominal fat ratio":
        numeric = _extract_numeric(value)
        return f"{numeric:.2f}" if numeric is not None else value

    if label == "Body mass index (BMI)":
        numeric = _extract_numeric(value)
        return f"{numeric:.1f} kg/m²" if numeric is not None else value

    if label == "Basal metabolic rate (BMR)":
        numeric = _extract_numeric(value)
        return f"{int(round(numeric))} kcal/day" if numeric is not None else value

    return value


def _body_comp_metric_map(rows: list[dict]) -> dict:
    out = {}
    for row in rows:
        name = (row.get("display_name") or "").strip()
        if not name:
            continue
        out[name] = row
    return out


def _body_comp_interpretation_lines(patient, metric_map: dict) -> list[str]:
    lines = []

    body_fat_pct = _extract_numeric(metric_map.get("Body fat percentage", {}).get("value"))
    bmi = _extract_numeric(metric_map.get("Body mass index (BMI)", {}).get("value"))
    bmr = _extract_numeric(metric_map.get("Basal metabolic rate (BMR)", {}).get("value"))
    body_weight = _extract_numeric(metric_map.get("Body weight", {}).get("value"))
    target_weight = _extract_numeric(metric_map.get("Target weight", {}).get("value"))
    abdominal_fat_ratio = _extract_numeric(metric_map.get("Abdominal fat ratio", {}).get("value"))
    intracellular_fluid = _extract_numeric(metric_map.get("Intracellular fluid (QRMA)", {}).get("value"))
    extracellular_fluid = _extract_numeric(metric_map.get("Extracellular fluid (QRMA)", {}).get("value"))
    lean_mass = _extract_numeric(metric_map.get("Muscle mass / muscle volume", {}).get("value"))
    body_fat_mass = _extract_numeric(metric_map.get("Body fat mass", {}).get("value"))

    sex = (getattr(patient, "sex", None) or "").strip().lower()
    age = getattr(patient, "age", None)

    if intracellular_fluid is not None and extracellular_fluid is not None:
        fluid_ratio = None
        if extracellular_fluid > 0:
            fluid_ratio = intracellular_fluid / extracellular_fluid

        if fluid_ratio is not None:
            if fluid_ratio < 1.2:
                lines.append("Hydration pattern may lean toward lower intracellular fluid relative to extracellular fluid, which can reflect weaker cellular hydration or tissue-quality reserve.")
            elif fluid_ratio > 2.5:
                lines.append("Hydration pattern shows a relatively high intracellular-to-extracellular fluid balance; correlate with symptoms and the wider scan rather than interpreting this in isolation.")
            else:
                lines.append("Hydration markers appear broadly balanced, without an obvious fluid-distribution concern on this page.")

    if body_fat_pct is not None:
        if sex == "female":
            if body_fat_pct < 18:
                lines.append("Body fat percentage is relatively lean for a female profile and should be interpreted alongside energy, hormones, resilience, and bone-health context.")
            elif body_fat_pct <= 32:
                lines.append("Body fat percentage sits in a broadly reasonable range for a female profile.")
            else:
                lines.append("Body fat percentage is on the higher side and may contribute to inflammatory, metabolic, or hormonal load.")
        elif sex == "male":
            if body_fat_pct < 10:
                lines.append("Body fat percentage is relatively lean for a male profile and should be interpreted alongside energy, resilience, and hormonal context.")
            elif body_fat_pct <= 25:
                lines.append("Body fat percentage sits in a broadly reasonable range for a male profile.")
            else:
                lines.append("Body fat percentage is on the higher side and may contribute to metabolic and inflammatory load.")
        else:
            if body_fat_pct < 12:
                lines.append("Body fat percentage appears relatively low and should be interpreted alongside nutritional reserve and energy status.")
            elif body_fat_pct > 30:
                lines.append("Body fat percentage appears elevated and may contribute to inflammatory or metabolic burden.")

    if bmi is not None:
        if bmi < 18.5:
            lines.append("Body mass index is in the low range, which may reflect reduced reserve, underweight tendency, or mismatch between intake and tissue needs.")
        elif bmi < 25:
            lines.append("Body mass index is broadly within the conventional range.")
        elif bmi < 30:
            lines.append("Body mass index is above the conventional range and may reflect increased metabolic load.")
        else:
            lines.append("Body mass index is substantially above the conventional range and may be relevant to metabolic, inflammatory, or cardiovascular burden.")

    if bmr is not None and body_weight is not None:
        if sex == "female":
            if bmr < 1100:
                lines.append("Basal metabolic rate appears relatively low for body size, which may fit with lower metabolic activity, reduced lean tissue demand, ageing, or endocrine slowing.")
            elif bmr > 1600:
                lines.append("Basal metabolic rate appears relatively robust for body size on this scan.")
        elif sex == "male":
            if bmr < 1400:
                lines.append("Basal metabolic rate appears relatively low for body size, which may fit with reduced metabolic activity, lower lean-tissue demand, or endocrine slowing.")
            elif bmr > 1900:
                lines.append("Basal metabolic rate appears relatively robust for body size on this scan.")

    if lean_mass is not None and body_fat_mass is not None:
        if lean_mass > body_fat_mass * 2:
            lines.append("Lean-tissue mass remains proportionally stronger than fat mass, which is generally a favourable structural pattern.")
        elif body_fat_mass > lean_mass:
            lines.append("Fat mass appears high relative to lean tissue, which may weaken metabolic flexibility and physical reserve.")

    if body_weight is not None and target_weight is not None:
        gap = body_weight - target_weight
        if abs(gap) >= 8:
            if gap > 0:
                lines.append("Current weight sits materially above the QRMA target-weight estimate, so weight-related metrics should be interpreted alongside metabolic and inflammatory findings.")
            else:
                lines.append("Current weight sits materially below the QRMA target-weight estimate, so nutritional reserve and lean-tissue support may deserve attention.")

    if abdominal_fat_ratio is not None:
        if abdominal_fat_ratio >= 1.0:
            lines.append("Abdominal fat ratio is relatively high, which may align with greater central metabolic burden.")
        elif abdominal_fat_ratio < 0.8:
            lines.append("Abdominal fat ratio does not suggest a strong central-adiposity pattern on this page.")

    if age is not None and age >= 60:
        lines.append("Given age-related shifts in lean mass, bone integrity, and metabolic rate, these body-composition findings should be interpreted alongside musculoskeletal, endocrine, and energy-related sections.")

    return lines[:6]


def _body_comp_summary_text(patient, metric_map: dict) -> str:
    body_fat_pct = _extract_numeric(metric_map.get("Body fat percentage", {}).get("value"))
    bmr = _extract_numeric(metric_map.get("Basal metabolic rate (BMR)", {}).get("value"))
    sex = (getattr(patient, "sex", None) or "").strip().lower()

    summary_parts = [
        "This section summarises QRMA-derived body composition and metabolic profile indicators, including hydration, lean tissue, adiposity, and weight-related measures."
    ]

    if body_fat_pct is not None:
        if sex == "female" and body_fat_pct <= 32:
            summary_parts.append("Body fat percentage appears broadly reasonable for the profile shown.")
        elif sex == "male" and body_fat_pct <= 25:
            summary_parts.append("Body fat percentage appears broadly reasonable for the profile shown.")
        elif body_fat_pct > 32:
            summary_parts.append("Adiposity measures appear elevated and may contribute to wider metabolic burden.")
        elif body_fat_pct < 12:
            summary_parts.append("The profile appears relatively lean and should be interpreted alongside energy, tissue reserve, and resilience.")

    if bmr is not None:
        if (sex == "female" and bmr < 1100) or (sex == "male" and bmr < 1400):
            summary_parts.append("Basal metabolic rate appears relatively low for body size on this scan.")
        elif (sex == "female" and bmr > 1600) or (sex == "male" and bmr > 1900):
            summary_parts.append("Basal metabolic rate appears relatively robust for body size on this scan.")

    summary_parts.append("Where QRMA terminology is unconventional, clinically clearer labels are shown while preserving the original category reference.")
    return " ".join(summary_parts)


def build_body_composition_block(report: ParsedReport):
    target = next(
        (
            s
            for s in dedupe_sections_by_title(report.sections)
            if is_body_composition_section(s.display_title or s.source_title)
        ),
        None,
    )

    if not target or not getattr(target, "parameters", None):
        return None

    grouped = {
        "Fluid & Hydration": [],
        "Lean Tissue & Structural Mass": [],
        "Adiposity & Fat Distribution": [],
        "Weight & Metabolic Indicators": [],
        "Other QRMA Body Composition Indices": [],
    }

    all_rows = []
    section_title = target.display_title or target.source_title

    for param in target.parameters:
        param = _overlay_definition(section_title, param)

        raw_name = param.source_name or ""
        mapped_name = body_comp_display_name(raw_name)
        clean_value = _normalise_body_comp_value(mapped_name, param.actual_value_text or "")

        dedupe_key = mapped_name.strip().lower()

        candidate = {
            "dedupe_key": dedupe_key,
            "marker": raw_name,
            "display_name": mapped_name,
            "range": param.normal_range_text or "",
            "value": clean_value,
            "severity": severity_display(param.severity),
            "severity_class": severity_class(param.severity),
            "status_pointer_position": status_pointer_position(param.severity, "standard"),
            "meaning": _meaning_text(param),
            "what_it_means": getattr(param, "what_it_means", None) or _meaning_text(param),
            "why_it_matters": getattr(param, "why_it_matters", None) or "",
            "functional_significance": getattr(param, "functional_significance", None) or "",
            "common_patterns": getattr(param, "common_patterns", None) or "",
            "your_result_summary": getattr(param, "patient_interpretation", None) or "",
        }

        existing = next((r for r in all_rows if r["dedupe_key"] == dedupe_key), None)
        if existing:
            existing_value = existing.get("value", "")
            if len(clean_value) > len(existing_value):
                existing.update(candidate)
            continue

        all_rows.append(candidate)

    metric_map = _body_comp_metric_map(all_rows)

    for row in all_rows:
        group_title = classify_body_comp_marker(row["display_name"])
        grouped[group_title].append(
            {
                "marker": row["marker"],
                "display_name": row["display_name"],
                "range": row["range"],
                "value": row["value"],
                "severity": row["severity"],
                "severity_class": row["severity_class"],
                "status_pointer_position": row["status_pointer_position"],
                "meaning": row["meaning"],
                "what_it_means": row["what_it_means"],
                "why_it_matters": row["why_it_matters"],
                "functional_significance": row["functional_significance"],
                "common_patterns": row["common_patterns"],
                "your_result_summary": row["your_result_summary"],
            }
        )

    return {
        "title": "Body Composition & Metabolic Profile",
        "source_title": target.source_title,
        "source_subtitle": "QRMA category: Element of Human",
        "priority": target.priority or "normal",
        "priority_label": priority_label(target.priority),
        "status_label": priority_label(target.priority),
        "abnormal_count": target.abnormal_count,
        "normal_count": target.normal_count,
        "summary": _body_comp_summary_text(report.patient, metric_map),
        "interpretation_lines": _body_comp_interpretation_lines(report.patient, metric_map),
        "groups": [{"title": k, "rows": v} for k, v in grouped.items() if v],
    }

def build_full_marker_tables(report: ParsedReport):
    tables = []
    for section in dedupe_sections_by_title(report.sections):
        if not getattr(section, "parameters", None) or len(section.parameters) == 0:
            continue
        if is_hidden_section(section.display_title or section.source_title):
            continue
        if is_body_composition_section(section.display_title or section.source_title):
            continue

        bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
        section_title = section.display_title or section.source_title
        rows = []
        seen = set()
        for param in section.parameters:
            key = (param.source_name, param.actual_value_text)
            if key in seen:
                continue
            seen.add(key)
            param = _overlay_definition(section_title, param)
            rows.append({
                "marker": param.source_name,
                "display_name": param.display_label or param.source_name,
                "range": param.normal_range_text or "",
                "value": param.actual_value_text or "",
                "severity": severity_display(param.severity),
                "severity_class": severity_class(param.severity),
                "status_pointer_position": status_pointer_position(param.severity, bar_variant),
                "status_bar_variant": bar_variant,
                "meaning": _meaning_text(param),
            })

        if not rows:
            continue

        tables.append({
            "title": normalise_display_term(section.display_title or section.source_title),
            "source_title": section.source_title,
            "priority": section.priority or "normal",
            "priority_label": priority_label(section.priority),
            "abnormal_count": section.abnormal_count,
            "normal_count": section.normal_count,
            "status_bar_variant": bar_variant,
            "rows": rows,
        })
    return tables


def build_toc_items(
    section_blocks,
    include_product_recommendations,
    product_recommendations,
    include_appendix,
    practitioner_notes,
    has_body_composition,
    recommendation_mode,
    clinical_recommendations,
    protocol_plan,
    complete_product_mapping,
):
    items = ["Cover", "At a glance", "Scan score overview"]

    if practitioner_notes.practitioner_summary:
        items.append("Practitioner summary")
    if practitioner_notes.recommendations:
        items.append("Practitioner recommendations")
    if practitioner_notes.follow_up_suggestions:
        items.append("Follow-up suggestions")

    if section_blocks:
        items.append("Priority category review")

    if has_body_composition:
        items.append("Body composition analysis")

    if clinical_recommendations:
        items.append("Clinical recommendations")

    mode = normalize_recommendation_mode(recommendation_mode)

    if products_enabled(mode):
        if protocol_plan:
            items.append("Recommended support plan")
        if complete_product_mapping:
            items.append("Complete product recommendation mapping")

    if section_blocks:
        items.append("Complete system marker tables")

    if include_product_recommendations and product_recommendations:
        items.append("Optional product recommendations")

    if include_appendix:
        items.append("Appendix")

    return items


def build_patient_header(report: ParsedReport):
    patient = report.patient
    age_or_dob_label = "DOB" if getattr(patient, "date_of_birth", None) else "Age"
    age_or_dob_value = patient.date_of_birth if getattr(patient, "date_of_birth", None) else (str(patient.age) if patient.age is not None else "")
    scan_datetime = f"{patient.scan_date} {patient.scan_time}" if patient.scan_date and patient.scan_time else (patient.scan_date or patient.scan_time or "")
    return {
        "name": patient.full_name or "",
        "sex": patient.sex or "",
        "age_or_dob_label": age_or_dob_label,
        "age_or_dob_value": age_or_dob_value,
        "scan_datetime": scan_datetime,
    }


def build_v2_key_patterns(report: ParsedReport) -> list[str]:
    patterns = getattr(report, "patterns", []) or []
    out = []

    for p in patterns[:4]:
        label = getattr(p, "label", None) or p.get("label", "Pattern")
        summary = getattr(p, "summary", None) or p.get("summary", "")
        if summary:
            out.append(summary)
        else:
            out.append(f"{label} identified as a clinically relevant cross-system pattern.")

    return out


def build_v2_priority_actions(report):
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    actions = []
    seen = set()

    def add_actions(pattern):
        for a in pattern.suggested_focus_areas:
            if a not in seen:
                seen.add(a)
                actions.append(a)

    # 1. Primary pattern first
    if primary:
        add_actions(primary)

    # 2. Then contributing
    for p in patterns[1:3]:
        add_actions(p)

    action_map = {
        "digestive support": "Strengthen digestive function to improve breakdown, tolerance, and nutrient availability.",
        "absorptive support": "Support intestinal absorption and assimilation of key nutrients.",
        "mucosal repair": "Prioritise mucosal repair and gut-lining resilience.",
        "foundational nutrient repletion": "Replete foundational nutrients to support repair, energy, and resilience.",
        "detoxification support": "Support detoxification capacity and clearance pathways.",
        "exposure review": "Review environmental and lifestyle exposures contributing to toxic load.",
        "barrier repair": "Support barrier integrity and tissue resilience.",
        "immune-modulatory support": "Support immune regulation and reduce inflammatory burden.",
        "neurocognitive nutrient support": "Target nutrient support for cognitive and nervous-system function.",
        "trace element repletion": "Replete key trace elements supporting metabolic and immune function.",
    }

    return [action_map.get(x, x) for x in actions][:5]

def build_v2_clinical_snapshot(report) -> str:
    primary = getattr(report, "primary_pattern", None)
    patterns = getattr(report, "patterns", []) or []

    if not primary:
        return getattr(report, "overall_summary", "")

    profile = str(getattr(report, "report_profile", "") or "").lower()
    primary_name = primary.label.lower()
    contributing = [p.label.lower() for p in patterns[1:4]]

    is_child = "child" in profile

    if "absorption" in primary_name:
        if is_child:
            lead = (
                "The scan most strongly points to a digestion and nutrient-assimilation picture, "
                "which may be influencing growth, energy, and overall resilience"
            )
        else:
            lead = (
                "The scan most strongly points to a digestion and nutrient-assimilation picture, "
                "with wider effects likely extending into energy, repair, and resilience"
            )
    elif "toxic" in primary_name:
        lead = (
            "The scan most strongly points to a toxic-load and clearance picture, "
            "with wider effects likely extending into immune balance, detoxification capacity, and resilience"
        )
    elif "inflammatory" in primary_name:
        lead = (
            "The scan most strongly points to an inflammatory and barrier-related picture, "
            "with wider effects likely extending into tissue resilience and immune balance"
        )
    elif "neuro" in primary_name:
        if is_child:
            lead = (
                "The scan most strongly points to a neurodevelopment and support picture, "
                "with wider effects likely extending into regulation, resilience, and development"
            )
        else:
            lead = (
                "The scan most strongly points to a neurocognitive support picture, "
                "with wider effects likely extending into regulation, resilience, and performance"
            )
    else:
        lead = (
            "The scan points to a dominant cross-system functional picture "
            "with broader effects on overall system balance"
        )

    if contributing:
        readable = " and ".join(contributing[:2])
        return f"{lead}. The wider picture also suggests overlap with {readable}."

    return f"{lead}."


def build_v2_practitioner_overview(report) -> str:
    primary = getattr(report, "primary_pattern", None)

    if not primary:
        return getattr(report, "practitioner_summary", "")

    name = primary.label.lower()
    focus = primary.suggested_focus_areas or []
    focus_text = ", ".join(focus[:3])

    if "absorption" in name:
        return (
            "From a practitioner perspective, the dominant pattern suggests impaired digestion, "
            "absorptive efficiency, or mucosal integrity may be contributing to wider downstream findings. "
            "This may help explain the broader nutrient picture and the knock-on effects seen across resilience, "
            f"repair, and overall function. Follow-up should prioritise {focus_text}."
        )

    if "toxic" in name:
        return (
            "From a practitioner perspective, the dominant pattern suggests a meaningful toxic-load or clearance burden. "
            "This may be amplifying oxidative stress, immune reactivity, and wider metabolic strain. "
            f"Follow-up should prioritise {focus_text}."
        )

    if "inflammatory" in name:
        return (
            "From a practitioner perspective, the dominant pattern suggests barrier stress and inflammatory reactivity "
            "may be reinforcing wider systemic imbalance. This may be relevant to tissue resilience, immune tone, "
            f"and symptom persistence. Follow-up should prioritise {focus_text}."
        )

    if "neuro" in name:
        return (
            "From a practitioner perspective, the dominant pattern suggests neurocognitive support needs within a broader "
            "functional picture. This may relate to nutrient sufficiency, membrane support, and nervous-system resilience. "
            f"Follow-up should prioritise {focus_text}."
        )

    return (
        "From a practitioner perspective, the dominant pattern suggests a broader upstream functional imbalance "
        f"that warrants targeted follow-up. Priority areas include {focus_text}."
    )


def build_report_context(report: ParsedReport, overrides: ReportOverrides | None = None):
    config = load_practitioner_config()
    overrides = overrides or ReportOverrides()

    all_non_body = [
        s for s in dedupe_sections_by_title(report.sections)
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
    ]

    scan_scores = compute_scan_scores(all_non_body)

    # 🔥 ADD THIS LINE (Pattern Engine V2)
    report = attach_pattern_engine_v2_output(report)

    section_card_map = {
        (card["title"] or "").strip().lower(): card
        for card in scan_scores.get("section_score_cards", [])
    }

    selected_sections = [
        s for s in select_report_sections(report, max_sections=config.get("max_sections", 6))
        if not is_body_composition_section(s.display_title or s.source_title)
        and not is_hidden_section(s.display_title or s.source_title)
        and getattr(s, "parameters", None)
        and len(s.parameters) > 0
    ]

    priority_overview = build_priority_overview(report)
    full_marker_tables = build_full_marker_tables(report)
    patient_header = build_patient_header(report)
    body_composition_block = build_body_composition_block(report)
    priority_sections = build_priority_sections_list(report)
    priority_section_intro = build_priority_category_review_intro(priority_sections)

    clinical_context = None

    recommendation_mode = normalize_recommendation_mode(
        os.getenv("REPORT_RECOMMENDATION_MODE", "natural_approaches_clinical")
    )

    products = resolve_all_products(
        report,
        recommendation_mode=recommendation_mode,
        clinical_context=clinical_context,
    )

    if products_enabled(recommendation_mode):
        protocol = compose_protocol(report, products)
        protocol = enrich_protocol_plan_with_narrative_v3(report, protocol)
        complete_mapping = build_complete_product_mapping(products)
    else:
        protocol = None
        complete_mapping = []

    section_blocks = []
    for section in selected_sections:
        marker_cards = []
        bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
        section_title = section.display_title or section.source_title
        section_card = section_card_map.get((section_title or "").strip().lower(), {})
        derived_score = section_card.get("score", section.section_score)
        derived_flagged = section_card.get("flagged_count", section.abnormal_count)
        derived_priority = _derived_priority(derived_score)

        for marker in select_priority_marker_cards(section, max_markers=config.get("max_markers_per_section", 4)):
            marker = _overlay_definition(section_title, marker)
            marker_cards.append({
                "source_name": marker.source_name,
                "display_name": marker.display_label or marker.source_name,
                "clinical_label": marker.clinical_label,
                "actual_value_text": marker.actual_value_text,
                "normal_range_text": marker.normal_range_text,
                "severity": marker.severity,
                "severity_display": severity_display(marker.severity),
                "severity_class": severity_class(marker.severity),
                "status_pointer_position": status_pointer_position(marker.severity, bar_variant),
                "status_bar_variant": bar_variant,
                "what_it_means": marker.what_it_means,
                "why_it_matters": marker.why_it_matters,
                "functional_significance": marker.functional_significance,
                "common_patterns": marker.common_patterns,
                "patient_interpretation": marker.patient_interpretation,
            })

        if not marker_cards or len(section.parameters) == 0:
            continue

        total = len(section.parameters or [])
        section_blocks.append({
            "source_title": section.source_title,
            "display_title": normalise_display_term(section.display_title or section.source_title),
            "priority": derived_priority,
            "priority_label": priority_label(derived_priority),
            "summary": section.summary,
            "top_findings": section.top_findings,
            "abnormal_count": derived_flagged,
            "normal_count": max(0, total - derived_flagged),
            "section_score": derived_score,
            "status_bar_variant": bar_variant,
            "markers": marker_cards,
            "flagged_count": derived_flagged,
            "within_count": max(0, total - derived_flagged),
            "total_count": total,
        })

    appendix_rows = []
    if config.get("include_appendix", True):
        for section in dedupe_sections_by_title(report.sections):
            if not getattr(section, "parameters", None) or len(section.parameters) == 0:
                continue
            if is_hidden_section(section.display_title or section.source_title):
                continue
            if is_body_composition_section(section.display_title or section.source_title):
                continue

            bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
            section_title = section.display_title or section.source_title
            seen = set()
            for param in section.parameters:
                key = (param.source_name, param.actual_value_text)
                if key in seen:
                    continue
                seen.add(key)
                param = _overlay_definition(section_title, param)
                appendix_rows.append({
                    "section": normalise_display_term(section.display_title or section.source_title),
                    "marker": param.source_name,
                    "display_name": param.display_label or param.source_name,
                    "range": param.normal_range_text,
                    "value": param.actual_value_text,
                    "severity": severity_display(param.severity),
                    "severity_class": severity_class(param.severity),
                    "status_pointer_position": status_pointer_position(param.severity, bar_variant),
                    "status_bar_variant": bar_variant,
                })

    include_appendix = config.get("include_appendix", True)
    include_product_recommendations = config.get("include_product_recommendations", False)


    overall_summary = normalise_recommendation_narrative(
        normalise_narrative_text(getattr(report, "overall_summary", None))
    )
    practitioner_summary = normalise_recommendation_narrative(
        normalise_narrative_text(getattr(report, "practitioner_summary", None))
    )
    key_patterns = [
        normalise_recommendation_narrative(normalise_narrative_text(x))
        for x in getattr(report, "key_patterns", [])
    ]
    priority_actions = [
        normalise_recommendation_narrative(normalise_narrative_text(x))
        for x in getattr(report, "priority_actions", [])
    ]

    if getattr(report, "patterns", None):
        key_patterns = [
            normalise_recommendation_narrative(normalise_narrative_text(x))
            for x in build_v2_key_patterns(report)
        ]
        priority_actions = [
            normalise_recommendation_narrative(normalise_narrative_text(x))
            for x in build_v2_priority_actions(report)
        ]
    else:
        key_patterns = [
            normalise_recommendation_narrative(normalise_narrative_text(x))
            for x in getattr(report, "key_patterns", [])
        ]
        priority_actions = [
            normalise_recommendation_narrative(normalise_narrative_text(x))
            for x in getattr(report, "priority_actions", [])
        ]

    clinical_recommendations = []
    for rec in rank_v2_clinical_recommendations(report):
        summary = normalise_narrative_text(rec.get("summary", ""))
        summary = normalise_recommendation_narrative(summary)

        rationale = rec.get("rationale", "")
        rationale = normalise_rationale_leading_section(rationale)

        clinical_recommendations.append({
            **rec,
            "title": normalise_recommendation_title(
                normalise_display_term(rec.get("title", ""))
            ),
            "summary": summary,
            "rationale": rationale,
        })

    clinical_recommendations = rewrite_clinical_recommendations_v3(clinical_recommendations)
    toc_items = build_toc_items(
            section_blocks=section_blocks,
            include_product_recommendations=include_product_recommendations,
            product_recommendations=getattr(report, "product_recommendations", []),
            include_appendix=include_appendix,
            practitioner_notes=overrides.practitioner_notes,
            has_body_composition=body_composition_block is not None,
            recommendation_mode=recommendation_mode,
            clinical_recommendations=clinical_recommendations,
            protocol_plan=protocol,
            complete_product_mapping=complete_mapping,
        )
        

    primary_pattern_context = (
        {
            "title": report.primary_pattern.label,
            "clinical_summary": report.primary_pattern.summary,
            "follow_up_focus": report.primary_pattern.suggested_focus_areas,
            "confidence": report.primary_pattern.confidence,
            "severity": report.primary_pattern.severity,
            "score": report.primary_pattern.score,
        }
        if getattr(report, "primary_pattern", None)
        else None
    )

    raw_contributing_patterns = (
        list(getattr(report, "contributing_patterns", []) or [])
        or list((getattr(report, "patterns", []) or [])[1:4])
    )

    contributing_patterns_context = [
        {
            "title": getattr(p, "label", None) or getattr(p, "title", None) or "",
            "clinical_summary": getattr(p, "summary", None) or getattr(p, "clinical_summary", None) or "",
            "follow_up_focus": getattr(p, "suggested_focus_areas", None) or getattr(p, "follow_up_focus", None) or [],
            "confidence": getattr(p, "confidence", None),
            "severity": getattr(p, "severity", None),
            "score": getattr(p, "score", None),
        }
        for p in raw_contributing_patterns
    ]


    glance_narrative = rewrite_at_a_glance_v3(
        report,
        build_v2_clinical_snapshot(report),
        build_v2_practitioner_overview(report),
        primary_pattern_context,
        contributing_patterns_context,
    )

    overall_summary = glance_narrative["overall_summary"]
    practitioner_summary = glance_narrative["practitioner_summary"]
    primary_pattern_context = glance_narrative["primary_pattern"]
    contributing_patterns_context = glance_narrative["contributing_patterns"]

        
    system_score_cards = [
        {
            **card,
            "included_sections": [
                normalise_display_term(x)
                for x in card.get("included_sections", [])
            ],
        }
        for card in scan_scores["system_score_cards"]
    ]

    report_generated_at = datetime.now(ZoneInfo("Europe/London")).strftime("%d/%m/%Y %H:%M %Z")
    build_version = os.getenv("REPORT_BUILD_VERSION", "dev")

    return {
        "config": config,
        "report_title": config.get("report_title", "Personalised Wellness Scan Report"),
        "clinic_name": config.get("brand_name", "Wellness Report"),
        "subtitle": config.get("subtitle", ""),
        "patient": report.patient,
        "patient_header": patient_header,
        "report_profile": getattr(report, "report_profile", None),
        "report_generated_at": report_generated_at,
        "build_version": build_version,
        "recommendation_mode": recommendation_mode,
        "overall_summary": overall_summary,
        "priority_sections": priority_sections,
        "priority_overview": priority_overview,
        "section_blocks": section_blocks,
        "body_composition_block": body_composition_block,
        "full_marker_tables": full_marker_tables,
        "appendix_rows": appendix_rows,
        "include_appendix": include_appendix,
        "include_toc": config.get("include_toc", True),
        "toc_items": toc_items,
        "clinical_recommendations": clinical_recommendations,
        "product_recommendations": getattr(report, "product_recommendations", []),
        "include_product_recommendations": include_product_recommendations,
        "practitioner_notes": overrides.practitioner_notes,
        "practitioner_summary": practitioner_summary,
        "key_patterns": key_patterns,
        "priority_actions": priority_actions,
        "priority_section_intro": priority_section_intro,
        "detected_patterns": getattr(report, "detected_patterns", []),
        "product_recommendations": products, #
        "protocol_plan": protocol, # 
        "complete_product_mapping": complete_mapping,

        "primary_pattern": primary_pattern_context,

        "contributing_patterns": contributing_patterns_context,

        "patterns": [
            {
                "name": p.label,
                "description": p.summary,
                "follow_up_focus": p.suggested_focus_areas,
                "confidence": p.confidence,
                "severity": p.severity,
                "score": p.score,
            }
            for p in getattr(report, "patterns", [])
        ],


        "category_completeness": getattr(report, "category_completeness", {}),
        "overall_scan_score": scan_scores["overall_score"],
        "overall_scan_band_label": scan_scores["overall_band_label"],
        "overall_scan_gauge_color": scan_scores["overall_gauge_color"],
        "system_score_cards": system_score_cards,
        "section_score_cards": scan_scores.get("section_score_cards", []),
        "body_system_cards": scan_scores.get("body_system_cards", []),
    }

def _safe_list(value):
    return value if isinstance(value, list) else []


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def build_viewer_payload(report: ParsedReport, overrides: ReportOverrides | None = None) -> dict:
    """
    Build a structured payload for the web viewer using the same context that powers
    the printable report, but without document-only concerns like page layout.
    """
    ctx = build_report_context(report, overrides=overrides)

    patient = ctx.get("patient")
    patient_header = ctx.get("patient_header", {}) or {}

    raw_overall_summary = ctx.get("overall_summary")
    if isinstance(raw_overall_summary, dict):
        overall_summary_payload = {
            "clinical_snapshot": raw_overall_summary.get("clinical_snapshot", "") or raw_overall_summary.get("summary", ""),
            "summary": raw_overall_summary.get("summary", "") or raw_overall_summary.get("clinical_snapshot", ""),
        }
    else:
        overall_summary_payload = {
            "clinical_snapshot": raw_overall_summary or "",
            "summary": raw_overall_summary or "",
        }

    raw_full_marker_tables = _safe_list(ctx.get("full_marker_tables"))
    enriched_full_marker_tables = []

    for section in raw_full_marker_tables:
        new_section = dict(section)
        new_rows = []

        for row in _safe_list(section.get("rows")):
            new_row = dict(row)
            new_row["what_it_means"] = row.get("what_it_means") or row.get("meaning") or ""
            new_row["why_it_matters"] = row.get("why_it_matters") or ""
            new_row["functional_significance"] = row.get("functional_significance") or ""
            new_row["common_patterns"] = row.get("common_patterns") or ""
            new_row["your_result_summary"] = row.get("patient_interpretation") or row.get("your_result_summary") or ""
            new_rows.append(new_row)

        new_section["rows"] = new_rows
        enriched_full_marker_tables.append(new_section)

    body_composition_block = _safe_dict(ctx.get("body_composition_block"))

    return {
        "tenant": {
            "brand_name": ctx.get("clinic_name"),
            "report_title": ctx.get("report_title"),
            "subtitle": ctx.get("subtitle"),
            "config": _safe_dict(ctx.get("config")),
        },
        "overview": {
            "patient": {
                "full_name": getattr(patient, "full_name", "") if patient else "",
                "sex": getattr(patient, "sex", "") if patient else "",
                "age": getattr(patient, "age", None) if patient else None,
                "height_cm": getattr(patient, "height_cm", None) if patient else None,
                "weight_kg": getattr(patient, "weight_kg", None) if patient else None,
                "scan_date": getattr(patient, "scan_date", "") if patient else "",
                "scan_time": getattr(patient, "scan_time", "") if patient else "",
                "scan_datetime": patient_header.get("scan_datetime", ""),
                "profile": ctx.get("report_profile"),
            },
            "overall_summary": overall_summary_payload,
            "practitioner_summary": ctx.get("practitioner_summary"),
            "primary_pattern": _safe_dict(ctx.get("primary_pattern")),
            "contributing_patterns": _safe_list(ctx.get("contributing_patterns")),
            "priority_actions": _safe_list(ctx.get("priority_actions")),
            "key_patterns": _safe_list(ctx.get("key_patterns")),
            "overall_scan_score": ctx.get("overall_scan_score"),
            "overall_scan_band_label": polished_overall_band_label(ctx.get("overall_scan_score")),
            "overall_scan_gauge_color": ctx.get("overall_scan_gauge_color"),
        },
        "systems": {
            "system_score_cards": _safe_list(ctx.get("system_score_cards")),
            "priority_overview": _safe_list(ctx.get("priority_overview")),
            "priority_sections": _safe_list(ctx.get("priority_sections")),
            "priority_section_intro": _safe_dict(ctx.get("priority_section_intro")),
            "section_score_cards": _safe_list(ctx.get("section_score_cards")),
            "body_system_cards": _safe_list(ctx.get("body_system_cards")),
        },
        "recommendations": {
            "clinical_recommendations": _safe_list(ctx.get("clinical_recommendations")),
            "protocol_plan": _safe_dict(ctx.get("protocol_plan")),
            "complete_product_mapping": _safe_list(ctx.get("complete_product_mapping")),
            "product_recommendations": _safe_list(ctx.get("product_recommendations")),
        },
        "detail": {
            "section_blocks": _safe_list(ctx.get("section_blocks")),
            "body_composition_block": body_composition_block,
            "full_marker_tables": enriched_full_marker_tables,
            "appendix_rows": _safe_list(ctx.get("appendix_rows")),
            "category_completeness": _safe_dict(ctx.get("category_completeness")),
            "patterns": _safe_list(ctx.get("patterns")),
            "detected_patterns": _safe_list(ctx.get("detected_patterns")),
        },
    }




def get_jinja_env():
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
    )


def render_report_html(report: ParsedReport, overrides: ReportOverrides | None = None) -> str:
    env = get_jinja_env()
    template = env.get_template("report.html")
    return template.render(**build_report_context(report, overrides=overrides))


async def build_report(
    report: ParsedReport,
    overrides: ReportOverrides | None = None,
    html_filename: str = "wellness_report.html",
    pdf_filename: str = "wellness_report.pdf",
) -> dict[str, str]:
    report_html = render_report_html(report, overrides=overrides)
    html_path = save_html(report_html, html_filename)
    pdf_path = await save_pdf(report_html, pdf_filename)
    viewer_payload = build_viewer_payload(report, overrides=overrides)

    return {
        "html": report_html,
        "html_path": html_path,
        "pdf_path": pdf_path,
        "viewer_payload": viewer_payload,
    }
