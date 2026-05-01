# app/services/interpreter.py

from app.models.schema import ParsedReport
from app.services.marker_library import get_marker_content, interpret_marker_result


def map_result_code(code: str | None):
    if not code:
        return ("unknown", False)

    code = code.upper()

    mapping = {
        "YC04": ("normal", False),
        "YC08": ("normal", False),

        "YC03": ("low_mild", True),
        "YC02": ("low_moderate", True),
        "YC01": ("low_severe", True),

        "YC05": ("high_mild", True),
        "YC06": ("high_moderate", True),
        "YC07": ("high_severe", True),
        "YC09": ("low_mild", True),
        "YC10": ("high_mild", True),
    }

    return mapping.get(code, ("unknown", False))


def enrich_report(report: ParsedReport) -> ParsedReport:
    for section in report.sections:
        for param in section.parameters:
            try:
                severity, abnormal = map_result_code(param.result_image_code)

                param.severity = severity
                param.is_abnormal = abnormal

                content = get_marker_content(param.source_name, section.source_title)

                if not param.what_it_means:
                    param.what_it_means = content.get("what_it_means")

                if not param.why_it_matters:
                    param.why_it_matters = content.get("why_it_matters")

                if not param.functional_significance:
                    param.functional_significance = content.get("functional_significance")

                if not param.common_patterns:
                    param.common_patterns = content.get("common_patterns")

                if not param.recommendation_notes:
                    param.recommendation_notes = content.get("recommendation_hint")

                if not param.marker_priority:
                    param.marker_priority = content.get("priority")
                
                if not param.patient_interpretation:
                    param.patient_interpretation = interpret_marker_result(
                        param.source_name,
                        severity,
                        section.source_title,
                    )
            except Exception as e:
                print(f"Error processing parameter: {param} - {str(e)}")

    return report
