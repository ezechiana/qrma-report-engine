from __future__ import annotations

from typing import Optional

from app.models.schema import ParsedReport, ParameterResult
from app.services.marker_definition_service import (
    get_marker_definition,
    load_marker_definition_index,
)


def _safe_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _determine_direction(marker: ParameterResult) -> str:
    status = _safe_text(getattr(marker, "status", None)).lower()
    severity = _safe_text(getattr(marker, "severity", None)).lower()

    combined = f"{status} {severity}"

    if any(token in combined for token in ["reduced", "low", "below"]):
        return "low"
    if any(token in combined for token in ["elevated", "high", "above", "raised"]):
        return "high"
    return "normal"


def _patient_interpretation(row: dict, marker: ParameterResult) -> str:
    direction = _determine_direction(marker)

    if direction == "low" and row.get("low_interpretation"):
        return row["low_interpretation"]
    if direction == "high" and row.get("high_interpretation"):
        return row["high_interpretation"]

    label = row.get("clinical_label") or row.get("original_name") or marker.source_name
    return f"{label} appears within the analyser's expected range in this scan."


def _apply_row(marker: ParameterResult, row: dict) -> ParameterResult:
    marker.control_id = row.get("control_id")
    marker.canonical_system = row.get("canonical_system")
    marker.clinical_label = row.get("clinical_label") or row.get("original_name") or marker.source_name
    marker.original_report_category = row.get("original_report_category")
    marker.display_label = marker.clinical_label

    marker.what_it_means = row.get("clinical_meaning") or marker.what_it_means
    marker.why_it_matters = row.get("why_it_matters") or marker.why_it_matters
    marker.functional_significance = row.get("functional_significance") or marker.functional_significance
    marker.common_patterns = row.get("pattern_links") or marker.common_patterns
    marker.patient_interpretation = _patient_interpretation(row, marker)
    marker.recommendation_notes = row.get("recommendation_hint") or marker.recommendation_notes
    marker.pattern_cluster = row.get("pattern_cluster") or marker.pattern_cluster

    return marker


def apply_catalog(report: ParsedReport) -> ParsedReport:
    index = load_marker_definition_index()

    for section in report.sections:
        section_title = section.display_title or section.source_title or ""
        section.original_report_category = (
            section_title
            if "Analysis Report Card" in section_title
            else f"({section_title}) Analysis Report Card"
        )

        for i, marker in enumerate(section.parameters):
            row = get_marker_definition(index, section.original_report_category, marker.source_name)
            if row:
                section.parameters[i] = _apply_row(marker, row)

        for marker in section.parameters:
            if getattr(marker, "canonical_system", None):
                section.canonical_system = marker.canonical_system
                break

    return report
