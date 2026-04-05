# app/services/overrides_service.py

from copy import deepcopy

from app.models.schema import ParsedReport, ReportOverrides


def apply_report_overrides(report: ParsedReport, overrides: ReportOverrides) -> ParsedReport:
    report_copy = deepcopy(report)

    # Section summary overrides
    for section in report_copy.sections:
        key_options = [
            section.display_title or "",
            section.source_title or "",
            section.normalized_key or "",
        ]
        for key in key_options:
            if key and key in overrides.section_summary_overrides:
                section.summary = overrides.section_summary_overrides[key]
                break

        # Marker comment overrides
        for param in section.parameters:
            marker_keys = [
                f"{section.display_title or section.source_title}::{param.source_name}",
                f"{section.source_title}::{param.source_name}",
                param.source_name,
            ]
            for marker_key in marker_keys:
                if marker_key in overrides.marker_comment_overrides:
                    param.patient_interpretation = overrides.marker_comment_overrides[marker_key]
                    break

    return report_copy