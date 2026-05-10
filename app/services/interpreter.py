# app/services/interpreter.py

from app.models.schema import ParsedReport
from app.services.marker_library import get_marker_content, interpret_marker_result


def _normalise_band_model(value: str | None) -> str:
    model = (value or "").strip().lower()
    if model in {"three_band", "3_band", "three", "compressed", "red_green_red"}:
        return "three_band"
    if model in {"seven_band", "7_band", "seven", "standard"}:
        return "seven_band"
    return "seven_band"


def severity_tier_for_severity(severity: str | None) -> int:
    sev = (severity or "unknown").strip().lower()
    if sev == "normal":
        return 0
    if sev in {"low", "high", "low_mild", "high_mild"}:
        return 1
    if sev in {"low_moderate", "high_moderate"}:
        return 2
    if sev in {"low_severe", "high_severe"}:
        return 3
    return 0


def map_result_code(code: str | None, band_model: str | None = None):
    if not code:
        return ("unknown", False)

    code = code.upper()
    model = _normalise_band_model(band_model)

    if code in {"YC04", "YC08"}:
        return ("normal", False)

    # Calibration v2: compressed QRMA red-green-red models do not contain
    # mild/moderate/severe sub-bands. Preserve direction only.
    if model == "three_band":
        low_codes = {"YC01", "YC02", "YC03", "YC09"}
        high_codes = {"YC05", "YC06", "YC07", "YC10"}
        if code in low_codes:
            return ("low", True)
        if code in high_codes:
            return ("high", True)
        return ("unknown", False)

    mapping = {
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
                band_model = getattr(param, "band_model", None) or "seven_band"
                severity, abnormal = map_result_code(param.result_image_code, band_model)

                param.band_model = band_model
                param.severity = severity
                param.is_abnormal = abnormal
                param.severity_tier = severity_tier_for_severity(severity)
                param.severity_rank = param.severity_tier

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
                    interpretation_severity = severity
                    if severity == "low":
                        interpretation_severity = "low_mild"
                    elif severity == "high":
                        interpretation_severity = "high_mild"

                    param.patient_interpretation = interpret_marker_result(
                        param.source_name,
                        interpretation_severity,
                        section.source_title,
                    )
            except Exception as e:
                print(f"Error processing parameter: {param} - {str(e)}")

    return report
