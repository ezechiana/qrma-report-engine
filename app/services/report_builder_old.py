# app/services/report_builder.py

from pathlib import Path
from typing import Any
from jinja2 import Environment, FileSystemLoader, select_autoescape
from app.models.schema import ParsedReport, ReportOverrides, ParameterResult
from app.services.config_service import load_practitioner_config
from app.services.pdf_service import save_html, save_pdf
from app.services.marker_definition_service import load_marker_definition_index, get_marker_definition
from app.services.scoring_engine import compute_scan_scores

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "app" / "templates"


def priority_label(priority: str | None) -> str:
    return {"high":"High priority","medium":"Medium priority","low":"Low priority","normal":"Within range"}.get(priority or "normal","Within range")

def severity_display(severity: str | None) -> str:
    mapping = {"normal":"Within range","low_mild":"Mildly reduced","low_moderate":"Moderately reduced","low_severe":"Markedly reduced","high_mild":"Mildly elevated","high_moderate":"Moderately elevated","high_severe":"Markedly elevated","unknown":"Unclear"}
    return mapping.get(severity or "unknown","Unclear")

def severity_class(severity: str | None) -> str:
    if severity in {"high_severe","low_severe"}: return "sev-severe"
    if severity in {"high_moderate","low_moderate"}: return "sev-moderate"
    if severity in {"high_mild","low_mild"}: return "sev-mild"
    return "sev-normal"

def status_pointer_position(severity: str | None, variant: str = "standard") -> str:
    if variant == "lung":
        mapping = {"low_severe":"10%","low_moderate":"22%","low_mild":"34%","normal":"50%","high_mild":"66%","high_moderate":"78%","high_severe":"90%","unknown":"50%"}
        return mapping.get(severity or "unknown","50%")
    mapping = {"low_severe":"8%","low_moderate":"24%","low_mild":"40%","normal":"50%","high_mild":"60%","high_moderate":"76%","high_severe":"92%","unknown":"50%"}
    return mapping.get(severity or "unknown","50%")

def status_bar_variant_for_section(section_title: str | None) -> str:
    return "lung" if (section_title or "").strip().lower() in {"lung function"} else "standard"

def is_body_composition_section(section_title: str | None) -> bool:
    return (section_title or "").strip().lower() in {"body composition analysis","element of human"}

def dedupe_sections_by_title(sections):
    seen, deduped = set(), []
    for section in sections:
        title = (section.display_title or section.source_title or "").strip().lower()
        if title and title not in seen:
            seen.add(title); deduped.append(section)
    return deduped

def select_report_sections(report: ParsedReport, max_sections: int = 6):
    deduped = dedupe_sections_by_title(report.sections)
    sorted_sections = sorted(deduped, key=lambda s: (s.section_score, s.abnormal_count), reverse=True)
    return [s for s in sorted_sections if s.priority != "normal"][:max_sections] or sorted_sections[:max_sections]

def select_priority_marker_cards(section, max_markers: int = 4):
    candidates = [p for p in section.parameters if p.severity in {"high_moderate","low_moderate","high_severe","low_severe"}]
    weights = {"high_severe":6,"low_severe":6,"high_moderate":5,"low_moderate":5}
    return sorted(candidates, key=lambda p: (weights.get(p.severity or "unknown",0), p.source_name.lower()), reverse=True)[:max_markers]

def build_priority_overview(report: ParsedReport):
    filtered = [s for s in dedupe_sections_by_title(report.sections) if not is_body_composition_section(s.display_title or s.source_title)]
    sorted_sections = sorted(filtered, key=lambda s: (s.section_score, s.abnormal_count), reverse=True)
    out = []
    for section in sorted_sections[:6]:
        total = len(section.parameters or [])
        out.append({"title": section.display_title or section.source_title,"priority": section.priority,"priority_label": priority_label(section.priority),"abnormal_count": section.abnormal_count,"section_score": section.section_score,"flagged_count": section.abnormal_count,"within_count": max(0,total-section.abnormal_count),"total_count": total})
    return out

def build_priority_sections_list(report: ParsedReport) -> list[str]:
    filtered = [s for s in dedupe_sections_by_title(report.sections) if not is_body_composition_section(s.display_title or s.source_title)]
    sorted_sections = sorted(filtered, key=lambda s: (s.section_score, s.abnormal_count), reverse=True)
    return [s.display_title or s.source_title for s in sorted_sections if (s.abnormal_count > 0 or s.section_score > 0)][:6]

def classify_body_comp_marker(name: str) -> str:
    n = (name or "").strip().lower()
    if any(term in n for term in ["intracellular fluid","extracellular fluid","body moisture","water","fluid","edema"]): return "Hydration and fluid balance"
    if any(term in n for term in ["protein","muscle","muscle volume","lean body weight","lean","fat free","fat-free"]): return "Lean mass and muscle"
    if any(term in n for term in ["body fat","fat content","fat analysis","fat mass","adipose"]): return "Fat-related measures"
    if any(term in n for term in ["weight","body mass","bmi","standard weight","height"]): return "Weight-related measures"
    return "Other body composition markers"

def _overlay_definition(section_title: str, marker: ParameterResult) -> ParameterResult:
    try:
        index = load_marker_definition_index()
        system = getattr(marker, "original_report_category", None) or section_title
        row = get_marker_definition(index, system, marker.source_name)
        if not row: return marker
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

def build_body_composition_block(report: ParsedReport):
    target = next((s for s in dedupe_sections_by_title(report.sections) if is_body_composition_section(s.display_title or s.source_title)), None)
    if not target: return None
    grouped = {"Hydration and fluid balance":[],"Lean mass and muscle":[],"Fat-related measures":[],"Weight-related measures":[],"Other body composition markers":[]}
    section_title = target.display_title or target.source_title
    for param in target.parameters:
        param = _overlay_definition(section_title, param)
        grouped[classify_body_comp_marker(param.display_label or param.source_name)].append({"marker":param.source_name,"display_name":param.display_label or param.source_name,"range":param.normal_range_text or "","value":param.actual_value_text or "","severity":severity_display(param.severity),"severity_class":severity_class(param.severity),"status_pointer_position":status_pointer_position(param.severity,"standard"),"meaning":_meaning_text(param)})
    return {"title": target.display_title or target.source_title,"source_title": target.source_title,"priority": target.priority or "normal","priority_label": priority_label(target.priority),"abnormal_count": target.abnormal_count,"normal_count": target.normal_count,"summary": target.summary or "This section summarises body composition-related patterns from the scan, including fluid balance, lean mass, fat-related measures, and weight-related indicators.","groups":[{"title":k,"rows":v} for k,v in grouped.items() if v]}

def build_full_marker_tables(report: ParsedReport):
    tables = []
    for section in dedupe_sections_by_title(report.sections):
        if is_body_composition_section(section.display_title or section.source_title): continue
        bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
        section_title = section.display_title or section.source_title
        rows = []
        for param in section.parameters:
            param = _overlay_definition(section_title, param)
            rows.append({"marker":param.source_name,"display_name":param.display_label or param.source_name,"range":param.normal_range_text or "","value":param.actual_value_text or "","severity":severity_display(param.severity),"severity_class":severity_class(param.severity),"status_pointer_position":status_pointer_position(param.severity, bar_variant),"status_bar_variant": bar_variant,"meaning":_meaning_text(param)})
        tables.append({"title":section.display_title or section.source_title,"source_title":section.source_title,"priority":section.priority or "normal","priority_label":priority_label(section.priority),"abnormal_count":section.abnormal_count,"normal_count":section.normal_count,"status_bar_variant":bar_variant,"rows":rows})
    return tables

def build_toc_items(section_blocks, include_product_recommendations, product_recommendations, include_appendix, practitioner_notes, has_body_composition):
    items = ["Cover","At a glance","Scan score overview"]
    if practitioner_notes.practitioner_summary: items.append("Practitioner summary")
    if practitioner_notes.recommendations: items.append("Practitioner recommendations")
    if practitioner_notes.follow_up_suggestions: items.append("Follow-up suggestions")
    for section in section_blocks: items.append(section["display_title"])
    if has_body_composition: items.append("Body composition analysis")
    if section_blocks: items.append("Clinical recommendations")
    items.append("Complete system marker tables")
    if include_product_recommendations and product_recommendations: items.append("Optional product recommendations")
    if include_appendix: items.append("Appendix")
    return items

def build_patient_header(report: ParsedReport):
    patient = report.patient
    age_or_dob_label = "DOB" if patient.date_of_birth else "Age"
    age_or_dob_value = patient.date_of_birth if patient.date_of_birth else (str(patient.age) if patient.age is not None else "")
    scan_datetime = f"{patient.scan_date} {patient.scan_time}" if patient.scan_date and patient.scan_time else (patient.scan_date or patient.scan_time or "")
    return {"name":patient.full_name or "","sex":patient.sex or "","age_or_dob_label":age_or_dob_label,"age_or_dob_value":age_or_dob_value,"scan_datetime":scan_datetime}

def build_report_context(report: ParsedReport, overrides: ReportOverrides | None = None):
    config = load_practitioner_config()
    overrides = overrides or ReportOverrides()
    all_non_body = [s for s in dedupe_sections_by_title(report.sections) if not is_body_composition_section(s.display_title or s.source_title)]
    scan_scores = compute_scan_scores(all_non_body)
    selected_sections = [s for s in select_report_sections(report, max_sections=config.get("max_sections",6)) if not is_body_composition_section(s.display_title or s.source_title)]
    priority_overview = build_priority_overview(report)
    full_marker_tables = build_full_marker_tables(report)
    patient_header = build_patient_header(report)
    body_composition_block = build_body_composition_block(report)
    priority_sections = build_priority_sections_list(report)

    section_blocks = []
    for section in selected_sections:
        marker_cards = []
        bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
        section_title = section.display_title or section.source_title
        for marker in select_priority_marker_cards(section, max_markers=config.get("max_markers_per_section",4)):
            marker = _overlay_definition(section_title, marker)
            marker_cards.append({"source_name":marker.source_name,"display_name":marker.display_label or marker.source_name,"clinical_label":marker.clinical_label,"actual_value_text":marker.actual_value_text,"normal_range_text":marker.normal_range_text,"severity":marker.severity,"severity_display":severity_display(marker.severity),"severity_class":severity_class(marker.severity),"status_pointer_position":status_pointer_position(marker.severity, bar_variant),"status_bar_variant":bar_variant,"what_it_means":marker.what_it_means,"why_it_matters":marker.why_it_matters,"functional_significance":marker.functional_significance,"common_patterns":marker.common_patterns,"patient_interpretation":marker.patient_interpretation})
        if not marker_cards:
            continue
        total = len(section.parameters or [])
        section_blocks.append({"source_title":section.source_title,"display_title":section.display_title or section.source_title,"priority":section.priority,"priority_label":priority_label(section.priority),"summary":section.summary,"top_findings":section.top_findings,"abnormal_count":section.abnormal_count,"normal_count":section.normal_count,"section_score":section.section_score,"status_bar_variant":bar_variant,"markers":marker_cards,"flagged_count":section.abnormal_count,"within_count":max(0,total-section.abnormal_count),"total_count":total})

    appendix_rows = []
    if config.get("include_appendix", True):
        for section in dedupe_sections_by_title(report.sections):
            if is_body_composition_section(section.display_title or section.source_title): continue
            bar_variant = status_bar_variant_for_section(section.display_title or section.source_title)
            section_title = section.display_title or section.source_title
            for param in section.parameters:
                param = _overlay_definition(section_title, param)
                appendix_rows.append({"section":section.display_title or section.source_title,"marker":param.source_name,"display_name":param.display_label or param.source_name,"range":param.normal_range_text,"value":param.actual_value_text,"severity":severity_display(param.severity),"severity_class":severity_class(param.severity),"status_pointer_position":status_pointer_position(param.severity, bar_variant),"status_bar_variant":bar_variant})

    include_appendix = config.get("include_appendix", True)
    include_product_recommendations = config.get("include_product_recommendations", False)
    toc_items = build_toc_items(section_blocks, include_product_recommendations, report.product_recommendations, include_appendix, overrides.practitioner_notes, body_composition_block is not None)

    return {"config":config,"report_title":config.get("report_title","Personalised Wellness Scan Report"),"clinic_name":config.get("brand_name","Wellness Report"),"subtitle":config.get("subtitle",""),"patient":report.patient,"patient_header":patient_header,"report_profile":report.report_profile,"overall_summary":report.overall_summary,"priority_sections":priority_sections,"priority_overview":priority_overview,"section_blocks":section_blocks,"body_composition_block":body_composition_block,"full_marker_tables":full_marker_tables,"appendix_rows":appendix_rows,"include_appendix":include_appendix,"include_toc":config.get("include_toc",True),"toc_items":toc_items,"clinical_recommendations":getattr(report, "clinical_recommendations", []),"product_recommendations":report.product_recommendations,"include_product_recommendations":include_product_recommendations,"practitioner_notes":overrides.practitioner_notes,"practitioner_summary":getattr(report, "practitioner_summary", None),"key_patterns":getattr(report, "key_patterns", []),"priority_actions":getattr(report, "priority_actions", []),"overall_scan_score":scan_scores["overall_score"],"overall_scan_band_label":scan_scores["overall_band_label"],"overall_scan_gauge_color":scan_scores["overall_gauge_color"],"system_score_cards":scan_scores["system_score_cards"]}

def get_jinja_env():
    return Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html","xml"]))

def render_report_html(report: ParsedReport, overrides: ReportOverrides | None = None) -> str:
    env = get_jinja_env()
    template = env.get_template("report.html")
    return template.render(**build_report_context(report, overrides=overrides))

async def build_report(report: ParsedReport, overrides: ReportOverrides | None = None, html_filename: str = "wellness_report.html", pdf_filename: str = "wellness_report.pdf") -> dict[str, str]:
    report_html = render_report_html(report, overrides=overrides)
    html_path = save_html(report_html, html_filename)
    pdf_path = await save_pdf(report_html, pdf_filename)
    return {"html": report_html, "html_path": html_path, "pdf_path": pdf_path}
