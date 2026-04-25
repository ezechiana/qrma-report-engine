from uuid import UUID

from app.services.report_service import _norm_key
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import Patient, User, Case, ReportVersion, CaseStatus, ReportStatus    
from app.schemas.patients import PatientCreate, PatientRead, PatientUpdate
from app.services.patient_service import create_patient, update_patient

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _serialise_patient(db: Session, patient: Patient) -> dict:
    case_count = (
        db.query(func.count(Case.id))
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
        or 0
    )

    report_count = (
        db.query(func.count(ReportVersion.id))
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
        or 0
    )

    latest_case_activity = (
        db.query(func.max(Case.updated_at))
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
    )

    latest_report_activity = (
        db.query(func.max(ReportVersion.generated_at))
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == patient.user_id)
        .scalar()
    )

    activity_candidates = [dt for dt in [patient.updated_at, latest_case_activity, latest_report_activity] if dt]
    last_activity_at = max(activity_candidates) if activity_candidates else None

    data_sources = ["QRMA"] if case_count > 0 else []

    return {
        "id": patient.id,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "full_name": getattr(patient, "full_name", None) or f"{patient.first_name} {patient.last_name}".strip(),
        "date_of_birth": patient.date_of_birth,
        "age": patient.age,
        "sex": patient.sex,
        "height_cm": patient.height_cm,
        "weight_kg": patient.weight_kg,
        "created_at": patient.created_at,
        "updated_at": patient.updated_at,
        "case_count": case_count,
        "report_count": report_count,
        "last_activity_at": last_activity_at,
        "data_sources": data_sources,
    }

def _get_health_index_from_snapshot(report: ReportVersion):
    snapshot = getattr(report, "metrics_snapshot", None)

    if not isinstance(snapshot, dict):
        return None

    value = snapshot.get("health_index")

    if isinstance(value, (int, float)):
        return float(value)

    return None


@router.get("", response_model=list[PatientRead])
def list_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patients = (
        db.query(Patient)
        .filter(Patient.user_id == current_user.id)
        .order_by(Patient.created_at.desc())
        .all()
    )
    return [_serialise_patient(db, patient) for patient in patients]


@router.post("", response_model=PatientRead)
def create_patient_endpoint(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = create_patient(db, current_user, payload)
    return _serialise_patient(db, patient)


@router.get("/{patient_id}", response_model=PatientRead)
def get_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return _serialise_patient(db, patient)


@router.patch("/{patient_id}", response_model=PatientRead)
def update_patient_endpoint(
    patient_id: UUID,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    updated = update_patient(db, patient, payload)
    return _serialise_patient(db, updated)


@router.delete("/{patient_id}")
def delete_patient(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    linked_cases = (
        db.query(func.count(Case.id))
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .scalar()
        or 0
    )

    if linked_cases > 0:
        raise HTTPException(
            status_code=409,
            detail="This patient has linked cases and cannot be deleted yet.",
        )

    db.delete(patient)
    db.commit()
    return {"ok": True}


@router.get("/{patient_id}/cases")
def list_patient_cases(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    cases = (
        db.query(Case)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
        .order_by(Case.created_at.desc())
        .all()
    )

    return [
        {
            "id": case.id,
            "title": case.title,
            "display_name": getattr(case, "title", None) or f"Case {str(case.id)[:8]}",
            "status": case.status.value if hasattr(case.status, "value") else str(case.status),
            "created_at": case.created_at,
            "updated_at": case.updated_at,
            "scan_datetime": getattr(case, "scan_datetime", None),
        }
        for case in cases
    ]


@router.get("/{patient_id}/reports")
def list_patient_reports(
    patient_id: UUID,
    include_archived: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    query = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(Case.patient_id == patient.id, Case.user_id == current_user.id)
    )

    if hasattr(ReportVersion, "is_archived") and not include_archived:
        query = query.filter(ReportVersion.is_archived == False)

    reports = query.order_by(ReportVersion.generated_at.desc()).all()

    return [
        {
            "id": report.id,
            "case_id": report.case_id,
            "version_number": report.version_number,
            "status": report.status.value if hasattr(report.status, "value") else str(report.status),
            "generated_at": report.generated_at,
            "display_name": getattr(report.case, "title", None) or f"Report {str(report.id)[:8]}",
            "scan_datetime": getattr(report.case, "scan_datetime", None) if getattr(report, "case", None) else None,
            "is_archived": bool(getattr(report, "is_archived", False)),
            "health_index": _get_health_index_from_snapshot(report),
        }
        for report in reports
    ]


SYSTEM_GROUPS = {
    "Cardiovascular": ["cardiovascular_and_cerebrovascular", "blood_lipids", "pulse_of_heart_and_brain"],
    "Metabolic": ["blood_sugar", "pancreatic_function", "obesity", "essential_fatty_acid", "fatty_acid", "lecithin", "trace_element", "vitamin", "amino_acid", "coenzyme"],
    "Digestive": ["gastrointestinal_function", "large_intestine_function", "liver_function", "gallbladder_function"],
    "Renal & Fluid Balance": ["kidney_function"],
    "Respiratory": ["lung_function", "respiratory_function"],
    "Neurocognitive": ["brain_nerve", "human_consciousness_level", "adhd", "adolescent_intelligence"],
    "Immune & Inflammatory": ["immune_system", "human_immunity", "allergy", "human_toxin", "heavy_metal"],
    "Endocrine & Hormonal": ["endocrine_system", "thyroid", "female_hormone", "male_hormone", "menstrual_cycle", "gynecology", "breast", "male_sexual_function", "sperm_and_semen", "prostate", "adolescent_growth_index"],
    "Musculoskeletal": ["bone_disease", "bone_mineral_density", "rheumatoid_bone_disease", "bone_growth_index", "collagen", "channels_and_collaterals"],
    "Skin & Barrier": ["skin", "eye"],
}


def _humanize_key(key: str) -> str:
    return (key or "").replace("_", " ").strip().title()


def _clean_category_name(value: str | None) -> str:
    text = (value or "").strip()
    if not text or text.lower() in {"marker", "markers", "none", "null", "unknown", "uncategorised", "uncategorized", "—", "-"}:
        return "Uncategorised"
    return _humanize_key(text) if "_" in text else text


def _metric_label(metric, fallback_key: str) -> str:
    if isinstance(metric, dict):
        for field in ("label", "display_name", "marker", "marker_name", "name", "title"):
            value = metric.get(field)
            if value:
                return str(value).strip()
    return _humanize_key(fallback_key)


def _metric_category(metric, fallback: str | None = None) -> str:
    if isinstance(metric, dict):
        for field in (
            "category",
            "category_name",
            "qrma_category",
            "system_category",
            "original_report_category",
            "source_category",
            "section",
            "section_title",
            "system",
            "group",
            "clinical_area",
            "area",
        ):
            value = metric.get(field)
            if value:
                cleaned = _clean_category_name(str(value))
                if cleaned != "Uncategorised":
                    return cleaned
    return _clean_category_name(fallback)


def _viewer_payload_from_report(report: ReportVersion) -> dict:
    report_json = getattr(report, "report_json", None)
    if isinstance(report_json, dict):
        viewer = report_json.get("viewer") or {}
        return viewer if isinstance(viewer, dict) else {}
    return {}


def _marker_category_lookup_from_report(report: ReportVersion) -> dict:
    """
    Reuse the report viewer payload that already powers the full marker tables.
    This keeps patient trends aligned with the categories shown in the generated report.
    """
    lookup = {}

    snapshot = getattr(report, "metrics_snapshot", None) or {}
    for key, metric in (snapshot.get("markers") or {}).items():
        if isinstance(metric, dict):
            category = _metric_category(metric, None)
            if category != "Uncategorised":
                lookup[_norm_key(key)] = category
                label = _metric_label(metric, key)
                lookup[_norm_key(label)] = category

    viewer = _viewer_payload_from_report(report)
    detail = viewer.get("detail") or {}

    for section in detail.get("full_marker_tables") or []:
        if not isinstance(section, dict):
            continue

        category = _clean_category_name(
            section.get("title")
            or section.get("display_title")
            or section.get("source_title")
            or section.get("section")
        )

        if category == "Uncategorised":
            continue

        for row in section.get("rows") or []:
            if not isinstance(row, dict):
                continue

            for field in ("display_name", "marker", "source_name", "clinical_label", "name", "title"):
                label = row.get(field)
                if label:
                    lookup[_norm_key(str(label))] = category

    for row in detail.get("appendix_rows") or []:
        if not isinstance(row, dict):
            continue

        category = _clean_category_name(row.get("section"))
        if category == "Uncategorised":
            continue

        for field in ("display_name", "marker", "source_name", "name", "title"):
            label = row.get(field)
            if label:
                lookup[_norm_key(str(label))] = category

    return lookup


def _compute_trend_summary(series_dict: dict):
    summary = []

    for key, points in series_dict.items():
        if not points:
            continue

        latest = points[-1]
        latest_val = latest.get("value")

        if latest_val is None:
            continue

        latest_status = latest.get("severity")
        label = latest.get("marker_label") or latest.get("label") or _humanize_key(key)
        category_name = _metric_category(latest, None)

        if category_name == "Uncategorised":
            for point in reversed(points):
                category_name = _metric_category(point, None)
                if category_name != "Uncategorised":
                    break

        if len(points) < 2:
            summary.append({
                "key": key,
                "label": label,
                "category": category_name,
                "first": None,
                "latest": round(float(latest_val), 3),
                "delta": None,
                "direction": "baseline",
                "status": latest_status,
                "band_changed": False,
                "has_trend": False,
                "is_baseline": True,
                "is_meaningful": False,
            })
            continue

        first = points[0]
        first_val = first.get("value")

        if first_val is None:
            continue

        delta = float(latest_val) - float(first_val)

        direction = "flat"
        if delta > 0:
            direction = "up"
        elif delta < 0:
            direction = "down"

        first_sev = (first.get("severity") or "").strip().lower()
        latest_sev = (latest.get("severity") or "").strip().lower()
        band_changed = bool(first_sev and latest_sev and first_sev != latest_sev)

        is_meaningful = band_changed or abs(delta) >= 0.001

        summary.append({
            "key": key,
            "label": label,
            "category": category_name,
            "first": round(float(first_val), 3),
            "latest": round(float(latest_val), 3),
            "delta": round(delta, 3),
            "direction": direction,
            "status": latest_status,
            "band_changed": band_changed,
            "has_trend": True,
            "is_baseline": False,
            "is_meaningful": is_meaningful,
        })

    summary.sort(
        key=lambda x: (
            x["has_trend"] is False,
            not x["band_changed"],
            not x["is_meaningful"],
            -abs(x["delta"] or 0),
        )
    )

    return summary


def _num(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _signed(value) -> str:
    n = _num(value)
    return f"{n:+.3f}".rstrip("0").rstrip(".")


def _trend_direction_word(direction: str | None) -> str:
    return {
        "up": "increased",
        "down": "decreased",
        "flat": "remained broadly stable",
        "baseline": "is baseline only",
    }.get(direction or "", "changed")


def _category_hotspots(trend_summary: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}

    for item in trend_summary or []:
        if not item.get("has_trend") or not item.get("is_meaningful"):
            continue

        category = _clean_category_name(item.get("category"))
        if category == "Uncategorised":
            category = "Other"

        bucket = buckets.setdefault(category, {
            "category": category,
            "count": 0,
            "band_changes": 0,
            "largest_abs_delta": 0.0,
            "largest_marker": None,
            "largest_delta": 0.0,
        })

        delta = _num(item.get("delta"))
        abs_delta = abs(delta)
        bucket["count"] += 1

        if item.get("band_changed"):
            bucket["band_changes"] += 1

        if abs_delta > bucket["largest_abs_delta"]:
            bucket["largest_abs_delta"] = abs_delta
            bucket["largest_marker"] = item.get("label") or item.get("key")
            bucket["largest_delta"] = delta

    hotspots = list(buckets.values())
    hotspots.sort(key=lambda x: (x["band_changes"], x["count"], x["largest_abs_delta"]), reverse=True)

    return hotspots[:5]



def _trend_confidence(report_count: int, meaningful_count: int, hotspot_count: int) -> dict:
    """Simple deterministic confidence label for non-diagnostic trend interpretation."""
    if report_count >= 3 and meaningful_count >= 5 and hotspot_count >= 1:
        return {"label": "High", "reason": f"consistent multi-marker pattern across {report_count} generated reports"}
    if report_count >= 2 and meaningful_count >= 3:
        return {"label": "Medium", "reason": f"trend detected across {report_count} generated reports"}
    if report_count >= 2:
        return {"label": "Low", "reason": "limited trend signal across the available reports"}
    return {"label": "Baseline", "reason": "requires at least two generated reports"}

def _build_trend_intelligence(
    trend_summary: list[dict],
    health_index_points: list[dict],
    system_group_averages: dict,
    report_count: int = 0,
) -> dict:
    """
    Deterministic, non-AI trend interpretation layer.

    It turns raw trend rows into practitioner-facing orientation:
    - what changed most
    - which categories are clustered
    - whether overall Health Index changed
    - where follow-up attention should go
    """
    trend_summary = trend_summary or []
    has_real_trend = any(item.get("has_trend") for item in trend_summary)

    meaningful = [
        item for item in trend_summary
        if item.get("has_trend") and item.get("is_meaningful")
    ]
    band_changes = [
        item for item in meaningful
        if item.get("band_changed")
    ]

    meaningful_sorted = sorted(meaningful, key=lambda x: abs(_num(x.get("delta"))), reverse=True)
    largest_increases = [x for x in meaningful_sorted if _num(x.get("delta")) > 0][:3]
    largest_decreases = [x for x in meaningful_sorted if _num(x.get("delta")) < 0][:3]
    hotspots = _category_hotspots(trend_summary)
    confidence = _trend_confidence(report_count or len(health_index_points or []), len(meaningful), len(hotspots))

    health_change = None
    health_direction = "stable"
    if len(health_index_points or []) >= 2:
        first = _num(health_index_points[0].get("value"))
        latest = _num(health_index_points[-1].get("value"))
        health_change = round(latest - first, 3)
        if health_change > 0:
            health_direction = "improved"
        elif health_change < 0:
            health_direction = "declined"

    cards = [
        {
            "label": "Tracked markers",
            "value": len(trend_summary),
            "detail": f"{len(meaningful)} significant; {len(band_changes)} band changes",
            "tone": "neutral",
            "confidence": confidence["label"],
        },
        {
            "label": "Largest increase",
            "value": _signed(largest_increases[0].get("delta")) if largest_increases else "—",
            "detail": _humanize_key(largest_increases[0].get("label") or largest_increases[0].get("key")) if largest_increases else "No upward change detected",
            "tone": "increase" if largest_increases else "neutral",
        },
        {
            "label": "Largest decrease",
            "value": _signed(largest_decreases[0].get("delta")) if largest_decreases else "—",
            "detail": _humanize_key(largest_decreases[0].get("label") or largest_decreases[0].get("key")) if largest_decreases else "No downward change detected",
            "tone": "decrease" if largest_decreases else "neutral",
        },
        {
            "label": "Health Index",
            "value": _signed(health_change) if health_change is not None else "Baseline",
            "detail": f"Overall trend {health_direction}" if health_change is not None else "Add another scan to calculate overall trend",
            "tone": "increase" if health_change and health_change > 0 else "decrease" if health_change and health_change < 0 else "neutral",
        },
    ]

    insights = []

    if not has_real_trend:
        insights.append({
            "title": "Baseline scan only",
            "body": "Trend intelligence will appear after at least two generated reports for this patient.",
            "tone": "neutral",
        })
    else:
        if health_change is not None and abs(health_change) >= 1:
            insights.append({
                "title": f"Overall Health Index {health_direction}",
                "body": f"Health Index changed by {_signed(health_change)} points across the available scans.",
                "tone": "increase" if health_change > 0 else "decrease",
            })

        if hotspots:
            top = hotspots[0]
            insight_body = (
                f"{top['count']} significant marker changes are clustered in {top['category']}. "
                f"Largest movement: {_humanize_key(top['largest_marker'])} ({_signed(top['largest_delta'])})."
            )
            if top["band_changes"]:
                insight_body += f" {top['band_changes']} marker(s) changed band."

            insights.append({
                "title": f"{top['category']}-related markers show the strongest clustered change pattern",
                "body": insight_body.replace("Largest movement:", "Most significant directional change:"),
                "tone": "warning" if top["band_changes"] else "neutral",
            })

        if largest_increases:
            item = largest_increases[0]
            insights.append({
                "title": "Most significant upward directional change",
                "body": f"{_humanize_key(item.get('label') or item.get('key'))} increased by {_signed(item.get('delta'))}.",
                "tone": "increase",
            })

        if largest_decreases:
            item = largest_decreases[0]
            insights.append({
                "title": "Most significant downward directional change",
                "body": f"{_humanize_key(item.get('label') or item.get('key'))} decreased by {_signed(item.get('delta'))}.",
                "tone": "decrease",
            })

        if band_changes:
            first_three = ", ".join(
                _humanize_key(item.get("label") or item.get("key"))
                for item in band_changes[:3]
            )
            insights.append({
                "title": "Band changes require clinical review",
                "body": f"{len(band_changes)} marker(s) changed classification band — this may indicate systemic recalibration. Review high-impact markers first: {first_three}.",
                "tone": "warning",
            })

    if not insights:
        insights.append({
            "title": "No major trend signal detected",
            "body": "Tracked markers are not showing a strong directional pattern across the available scans.",
            "tone": "neutral",
        })

    focus_areas = [
        {
            "category": item["category"],
            "count": item["count"],
            "band_changes": item["band_changes"],
            "largest_marker": _humanize_key(item["largest_marker"]),
            "largest_delta": round(item["largest_delta"], 3),
        }
        for item in hotspots
    ]

    return {
        "has_trend": has_real_trend,
        "summary": (
            "Trend intelligence is based on marker movement, band changes, and category clustering across generated reports."
            if has_real_trend else
            "Trend intelligence requires at least two generated reports for this patient."
        ),
        "cards": cards,
        "insights": insights[:5],
        "focus_areas": focus_areas,
        "confidence": confidence,
    }


def _point(report: ReportVersion, value: float, meta: dict | None = None):
    case = getattr(report, "case", None)
    scan_dt = case.scan_datetime if case and case.scan_datetime else None
    generated_at = report.generated_at

    payload = {
        "report_id": str(report.id),
        "case_id": str(report.case_id),
        "label": case.title if case else f"Report v{report.version_number}",
        "version_number": report.version_number,
        "generated_at": generated_at.isoformat() if generated_at else None,
        "scan_datetime": scan_dt.isoformat() if scan_dt else None,
        "value": float(value),
        "is_archived": bool(getattr(report, "is_archived", False)),
    }

    if meta:
        payload.update(meta)

    return payload


def _metric_value(metric):
    if isinstance(metric, (int, float)):
        return float(metric)
    if isinstance(metric, dict):
        value = metric.get("value")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _metric_meta(metric, fallback_category: str | None = None, fallback_label: str | None = None):
    if isinstance(metric, dict):
        return {
            "range": metric.get("range"),
            "severity": metric.get("severity"),
            "severity_class": metric.get("severity_class"),
            "status_pointer_position": metric.get("status_pointer_position"),
            "status_bar_variant": metric.get("status_bar_variant"),
            "color": metric.get("color"),
            "unit": metric.get("unit"),
            "marker_label": _metric_label(metric, fallback_label or ""),
            "category": _metric_category(metric, fallback_category),
        }

    meta = {}
    if fallback_category:
        meta["category"] = _clean_category_name(fallback_category)
    if fallback_label:
        meta["marker_label"] = _humanize_key(fallback_label)
    return meta


# =========================
# TREND-AWARE RECOMMENDATION NARRATIVES
# =========================

def _safe_text(value) -> str:
    return str(value or "").strip()


def _normalise_text(value: str | None) -> str:
    return _safe_text(value).lower().replace("_", " ").strip()


def _viewer_recommendations_from_report(report: ReportVersion) -> dict:
    """
    Reuse the existing report recommendation payload.
    This avoids creating an independent recommendation engine.
    """
    report_json = getattr(report, "report_json", None)
    if not isinstance(report_json, dict):
        return {}

    viewer = report_json.get("viewer") or {}
    if not isinstance(viewer, dict):
        return {}

    recommendations = viewer.get("recommendations") or {}
    return recommendations if isinstance(recommendations, dict) else {}


def _recommendation_mode_from_report(report: ReportVersion) -> str:
    raw = getattr(report, "recommendation_mode", None)
    return raw.value if hasattr(raw, "value") else str(raw or "natural_approaches_clinical")


def _recommendations_are_enabled(mode: str | None) -> bool:
    return (mode or "").strip().lower() != "recommendations_off"


def _clean_trend_marker_label(value: str | None) -> str:
    text = _safe_text(value)
    for suffix in (" Significant Band", " Significant"):
        if text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)]
    return " ".join(text.split())


def _trend_item_category(item: dict) -> str:
    return _clean_category_name(
        item.get("category")
        or item.get("category_name")
        or item.get("qrma_category")
        or item.get("system")
        or item.get("group")
        or "General"
    )


def _direction_phrase(direction: str | None) -> str:
    if direction == "up":
        return "increased"
    if direction == "down":
        return "decreased"
    if direction == "flat":
        return "remained broadly stable"
    return "changed"


def _delta_text(delta) -> str:
    try:
        value = float(delta)
    except Exception:
        return ""
    return f"{value:+.3f}".rstrip("0").rstrip(".")


def _trend_matches_product(product: dict, trend_item: dict) -> bool:
    marker_label = _normalise_text(trend_item.get("label"))
    marker_key = _normalise_text(trend_item.get("key"))
    category = _normalise_text(_trend_item_category(trend_item))

    supporting_markers = [_normalise_text(x) for x in product.get("supporting_markers", []) or []]
    supporting_sections = [_normalise_text(x) for x in product.get("supporting_sections", []) or []]
    focus_area = _normalise_text(product.get("focus_area"))
    pattern_alignment = _normalise_text(product.get("pattern_alignment"))
    rationale = _normalise_text(product.get("rationale"))

    haystacks = supporting_markers + supporting_sections + [focus_area, pattern_alignment, rationale]

    for h in haystacks:
        if not h:
            continue
        if marker_label and (marker_label in h or h in marker_label):
            return True
        if marker_key and (marker_key in h or h in marker_key):
            return True
        if category and (category in h or h in category):
            return True

    return False


def _top_trend_evidence_for_product(product: dict, trend_summary: list[dict], limit: int = 3) -> list[dict]:
    matches = []
    for item in trend_summary or []:
        if item.get("has_trend") is not True:
            continue
        if item.get("is_meaningful") is not True and item.get("band_changed") is not True:
            continue
        if _trend_matches_product(product, item):
            matches.append(item)

    def score(item: dict) -> float:
        try:
            delta_score = abs(float(item.get("delta") or 0))
        except Exception:
            delta_score = 0
        if item.get("band_changed") is True:
            delta_score += 1000
        if item.get("is_meaningful") is True:
            delta_score += 100
        return delta_score

    matches.sort(key=score, reverse=True)
    return matches[:limit]


def _score_product_with_trend(product: dict, evidence: list[dict]) -> tuple[int, list[str]]:
    score = 0
    flags = set()

    for item in evidence:
        if item.get("is_meaningful"):
            score += 3
            flags.add("meaningful_change")
        if item.get("band_changed"):
            score += 4
            flags.add("band_shift")
        if item.get("direction") in {"up", "down"}:
            score += 1

    if len(product.get("supporting_sections") or []) >= 2:
        score += 2
        flags.add("multi_system")

    return score, sorted(flags)


def _evidence_sentence(evidence: list[dict]) -> str:
    if not evidence:
        return ""

    parts = []
    for item in evidence[:3]:
        label = _clean_trend_marker_label(item.get("label"))
        category = _trend_item_category(item)
        direction = _direction_phrase(item.get("direction"))
        delta = _delta_text(item.get("delta"))

        if delta:
            parts.append(f"{label} ({category}) {direction} by {delta}")
        else:
            parts.append(f"{label} ({category}) {direction}")

    return "Trend evidence: " + "; ".join(parts) + "."


def _priority_sentence(priority: str, flags: list[str]) -> str:
    flag_set = set(flags or [])

    if priority == "high":
        if "band_shift" in flag_set:
            return (
                "This deserves higher practitioner attention because one or more related markers "
                "changed classification band across scans."
            )
        return (
            "This deserves higher practitioner attention because related markers show meaningful "
            "movement across the available scan history."
        )

    if priority == "medium":
        return (
            "This may be clinically relevant as a supporting consideration because related findings "
            "show a measurable trend signal."
        )

    return (
        "This remains a lower-priority support consideration unless it aligns with symptoms, history, "
        "or practitioner assessment."
    )


def _safety_sentence(product: dict) -> str:
    source = _safe_text(product.get("source_label") or product.get("source"))
    label = f" {source}" if source else ""
    return (
        f"Review suitability, contraindications, allergies, current medicines, and clinical context "
        f"before using this{label} recommendation."
    )


def _enhance_product_with_trend_narrative(product: dict, trend_summary: list[dict]) -> dict:
    item = dict(product or {})
    evidence = _top_trend_evidence_for_product(item, trend_summary)
    trend_score, flags = _score_product_with_trend(item, evidence)

    priority = "high" if trend_score >= 6 else "medium" if trend_score >= 3 else "low"

    original_rationale = _safe_text(item.get("rationale"))
    evidence_text = _evidence_sentence(evidence)
    priority_text = _priority_sentence(priority, flags)

    explanation_parts = []
    if original_rationale:
        explanation_parts.append(original_rationale)
    if evidence_text:
        explanation_parts.append(evidence_text)
    explanation_parts.append(priority_text)

    item["trend_priority"] = priority
    item["trend_flags"] = flags
    item["trend_narrative"] = {
        "summary": " ".join(explanation_parts),
        "why_now": priority_text,
        "trend_evidence": evidence_text,
        "safety_note": _safety_sentence(item),
        "review_required": True,
        "evidence_markers": [
            {
                "key": e.get("key"),
                "label": _clean_trend_marker_label(e.get("label")),
                "category": _trend_item_category(e),
                "latest": e.get("latest"),
                "delta": e.get("delta"),
                "direction": e.get("direction"),
                "status": e.get("status"),
                "band_changed": bool(e.get("band_changed")),
            }
            for e in evidence
        ],
    }

    return item


def _add_trend_narratives_to_protocol(protocol: dict, enhanced_products: list[dict]) -> dict:
    if not isinstance(protocol, dict):
        return {}

    by_name = {p.get("name"): p for p in enhanced_products or [] if p.get("name")}
    protocol_out = dict(protocol)
    phases = []

    for phase in protocol.get("phases") or []:
        if not isinstance(phase, dict):
            continue
        phase_out = dict(phase)
        products = []
        for product in phase.get("products") or []:
            if not isinstance(product, dict):
                continue
            enriched = dict(product)
            match = by_name.get(product.get("name"))
            if match:
                enriched["trend_priority"] = match.get("trend_priority")
                enriched["trend_flags"] = match.get("trend_flags", [])
                enriched["trend_narrative"] = match.get("trend_narrative")
            products.append(enriched)
        phase_out["products"] = products
        phases.append(phase_out)

    protocol_out["phases"] = phases
    return protocol_out





def _priority_level_from_trend(*, count: int = 0, band_changes: int = 0, largest_delta: float = 0.0) -> str:
    """Convert deterministic trend strength into a practitioner-facing priority band."""
    abs_delta = abs(_num(largest_delta))
    if band_changes >= 3 or count >= 10 or abs_delta >= 25:
        return "high"
    if band_changes >= 1 or count >= 4 or abs_delta >= 5:
        return "medium"
    return "low"


def _priority_tone(priority: str | None) -> str:
    return {
        "high": "warning",
        "medium": "increase",
        "low": "neutral",
    }.get((priority or "").lower(), "neutral")


def _priority_rank(priority: str | None) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get((priority or "").lower(), 3)


def _priority_title_for_category(category: str | None) -> str:
    category = _clean_category_name(category)
    if category in {"Other", "Uncategorised"}:
        return "Cross-system review"
    return f"{category} review"


def _build_top_clinical_priorities(
    *,
    trend_summary: list[dict],
    trend_intelligence: dict | None = None,
    trend_recommendations: dict | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Deterministic top-priority engine for the patient detail screen.

    This does not prescribe. It ranks the areas that deserve practitioner attention
    using the same trend evidence already shown in the UI:
    - clustered category changes
    - band changes
    - largest directional movements
    - existing mode-safe recommendation priorities, where available
    """
    trend_summary = trend_summary or []
    trend_intelligence = trend_intelligence or {}
    trend_recommendations = trend_recommendations or {}

    priorities: list[dict] = []
    seen_titles: set[str] = set()

    def add_priority(item: dict) -> None:
        title = (item.get("title") or "Clinical review").strip()
        key = title.lower()
        if not title or key in seen_titles:
            return
        seen_titles.add(key)
        priorities.append(item)

    # 1) Strongest clustered categories from trend intelligence / category hotspots.
    hotspots = trend_intelligence.get("focus_areas") or _category_hotspots(trend_summary)
    for hotspot in hotspots[:limit]:
        category = hotspot.get("category") or "Other"
        count = int(_num(hotspot.get("count"), 0))
        band_changes = int(_num(hotspot.get("band_changes"), 0))
        largest_marker = hotspot.get("largest_marker") or "key marker"
        largest_delta = _num(hotspot.get("largest_delta"), 0)
        priority = _priority_level_from_trend(
            count=count,
            band_changes=band_changes,
            largest_delta=largest_delta,
        )

        reason = (
            f"{count} significant marker change(s) cluster in {category}. "
            f"Largest movement: {_humanize_key(largest_marker)} ({_signed(largest_delta)})."
        )
        if band_changes:
            reason += f" {band_changes} marker(s) changed classification band."

        add_priority({
            "title": _priority_title_for_category(category),
            "category": _clean_category_name(category),
            "priority": priority,
            "tone": _priority_tone(priority),
            "reason": reason,
            "evidence": {
                "marker_count": count,
                "band_changes": band_changes,
                "largest_marker": _humanize_key(largest_marker),
                "largest_delta": round(largest_delta, 3),
            },
            "source": "trend_cluster",
        })

    # 2) Add individual high-impact marker movements if they are not already represented.
    meaningful = [
        item for item in trend_summary
        if item.get("has_trend") and item.get("is_meaningful")
    ]
    meaningful_sorted = sorted(meaningful, key=lambda x: abs(_num(x.get("delta"))), reverse=True)

    for item in meaningful_sorted:
        if len(priorities) >= limit:
            break
        category = _clean_category_name(item.get("category") or "Other")
        label = _humanize_key(item.get("label") or item.get("key") or "Marker")
        delta = _num(item.get("delta"), 0)
        band_changed = bool(item.get("band_changed"))
        priority = _priority_level_from_trend(
            count=1,
            band_changes=1 if band_changed else 0,
            largest_delta=delta,
        )
        add_priority({
            "title": f"{label} follow-up",
            "category": category,
            "priority": priority,
            "tone": _priority_tone(priority),
            "reason": (
                f"{label} {_trend_direction_word(item.get('direction'))} by {_signed(delta)}. "
                f"Current status: {item.get('status') or 'Unclear'}."
            ),
            "evidence": {
                "marker": label,
                "delta": round(delta, 3),
                "direction": item.get("direction"),
                "band_changed": band_changed,
            },
            "source": "marker_movement",
        })

    # 3) Fill remaining slots from existing trend-aware recommendations.
    products = trend_recommendations.get("products") if isinstance(trend_recommendations, dict) else []
    products = sorted(
        [p for p in products or [] if isinstance(p, dict)],
        key=lambda p: (_priority_rank(p.get("trend_priority")), p.get("name") or ""),
    )
    for product in products:
        if len(priorities) >= limit:
            break
        focus = product.get("focus_area") or product.get("pattern_alignment") or product.get("source_label") or "Support consideration"
        focus_label = _humanize_key(str(focus))
        priority = product.get("trend_priority") or "low"
        narrative = product.get("trend_narrative") or {}
        add_priority({
            "title": focus_label,
            "category": focus_label,
            "priority": priority,
            "tone": _priority_tone(priority),
            "product": product.get("name"),
            "reason": narrative.get("summary") or product.get("rationale") or "Aligned with the current recommendation mode and scan context.",
            "source": "recommendation_context",
        })

    priorities.sort(key=lambda item: (_priority_rank(item.get("priority")), item.get("title") or ""))
    return priorities[:limit]

def _extract_top_trend_recommendation_priorities(trend_recommendations: dict, limit: int = 3) -> list[dict]:
    """Backward-compatible wrapper for recommendation-only priorities."""
    return _build_top_clinical_priorities(
        trend_summary=[],
        trend_intelligence={},
        trend_recommendations=trend_recommendations,
        limit=limit,
    )

def _build_clinical_narrative_summary(
    *,
    trend_summary: list[dict],
    trend_intelligence: dict,
    trend_recommendations: dict,
    report_count: int,
    health_index_points: list[dict],
) -> dict:
    """
    Deterministic practitioner-facing narrative summary.

    This is intentionally not a treatment generator. It summarises the longitudinal
    signal and points the practitioner toward existing, mode-safe support
    considerations.
    """
    trend_summary = trend_summary or []
    intelligence = trend_intelligence or {}
    confidence = intelligence.get("confidence") or _trend_confidence(report_count, 0, 0)
    focus_areas = intelligence.get("focus_areas") or []

    meaningful = [x for x in trend_summary if x.get("has_trend") and x.get("is_meaningful")]
    band_changes = [x for x in meaningful if x.get("band_changed")]

    health_change = None
    health_phrase = "Overall Health Index is not yet comparable."
    health_short = "Health Index is not yet comparable."
    if len(health_index_points or []) >= 2:
        first = _num((health_index_points or [])[0].get("value"))
        latest = _num((health_index_points or [])[-1].get("value"))
        health_change = round(latest - first, 3)
        if health_change > 0:
            health_phrase = f"Overall Health Index improved by {_signed(health_change)} points."
            health_short = f"Health Index improved by {_signed(health_change)} points."
        elif health_change < 0:
            health_phrase = f"Overall Health Index declined by {_signed(health_change)} points."
            health_short = f"Health Index declined by {_signed(health_change)} points."
        else:
            health_phrase = "Overall Health Index remained broadly stable."
            health_short = "Health Index remained broadly stable."

    focus_names = [x.get("category") for x in focus_areas[:3] if x.get("category")]
    focus_phrase = ", ".join(focus_names) if focus_names else "the available marker set"

    priorities = _build_top_clinical_priorities(
        trend_summary=trend_summary,
        trend_intelligence=intelligence,
        trend_recommendations=trend_recommendations,
        limit=5,
    )
    priority_names = [p.get("category") or p.get("title") for p in priorities[:3] if p.get("category") or p.get("title")]
    priority_phrase = ", ".join(priority_names) if priority_names else focus_phrase

    if report_count < 2:
        headline = "Baseline scan recorded. Trend interpretation will appear after a follow-up report."
        details = (
            "This patient currently has a baseline scan only. The system can record the current pattern, "
            "but trend interpretation and trend-aware support considerations require at least one follow-up report."
        )
    else:
        headline = (
            f"Across {report_count} generated reports, {health_short} "
            f"Priority review areas: {priority_phrase}."
        )
        details = (
            f"Across {report_count} generated reports, {len(meaningful)} marker(s) show meaningful movement "
            f"and {len(band_changes)} marker(s) changed classification band. {health_phrase} "
            f"The strongest clustered changes are currently concentrated around {focus_phrase}. "
            "These findings should be used to guide practitioner review and to prioritise, not replace, clinical judgement."
        )

    chart_context = "This chart provides visual confirmation of the longitudinal movement described above."
    if focus_areas:
        chart_context = (
            f"This chart should be interpreted alongside the strongest clustered changes: "
            f"{', '.join([x.get('category') for x in focus_areas[:3] if x.get('category')])}."
        )
    if health_change is not None and abs(health_change) >= 1:
        chart_context += f" Health Index movement over the same period is {_signed(health_change)}."

    return {
        "title": "Clinical narrative summary",
        "headline": headline,
        "body": details,
        "details": details,
        "confidence": confidence,
        "top_priorities": priorities,
        "chart_context": chart_context,
        "review_note": "AI-assisted summary for practitioner review only. Confirm against case history, symptoms, medications, allergies, and clinical judgement before making recommendations.",
    }

def _build_trend_recommendations(latest_report: ReportVersion | None, trend_summary: list[dict]) -> dict:
    if not latest_report:
        return {
            "mode": "unknown",
            "review_required": True,
            "disclaimer": "No ready report is available yet for recommendation support.",
            "products": [],
            "protocol": {},
        }

    mode = _recommendation_mode_from_report(latest_report)
    if not _recommendations_are_enabled(mode):
        return {
            "mode": mode,
            "review_required": True,
            "disclaimer": "Recommendations are disabled for the current recommendation mode.",
            "products": [],
            "protocol": {},
        }

    recs = _viewer_recommendations_from_report(latest_report)
    products = recs.get("product_recommendations") or []
    protocol = recs.get("protocol_plan") or {}

    enhanced_products = [
        _enhance_product_with_trend_narrative(product, trend_summary)
        for product in products
        if isinstance(product, dict)
    ]

    enhanced_products.sort(
        key=lambda p: (
            {"high": 0, "medium": 1, "low": 2}.get(p.get("trend_priority"), 3),
            p.get("name") or "",
        )
    )

    return {
        "mode": mode,
        "review_required": True,
        "disclaimer": (
            "Trend-aware support considerations are generated from existing mode-based recommendations "
            "and longitudinal scan changes. Practitioner review is required before use."
        ),
        "products": enhanced_products,
        "protocol": _add_trend_narratives_to_protocol(protocol, enhanced_products),
    }


@router.get("/{patient_id}/trends")
def get_patient_trends(
    patient_id: UUID,
    include_archived: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    patient = (
        db.query(Patient)
        .filter(Patient.id == patient_id, Patient.user_id == current_user.id)
        .first()
    )
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    query = (
        db.query(ReportVersion)
        .join(Case, Case.id == ReportVersion.case_id)
        .filter(
            Case.patient_id == patient.id,
            Case.user_id == current_user.id,
            ReportVersion.status == ReportStatus.ready,
        )
        .order_by(ReportVersion.generated_at.desc())
    )

    if hasattr(ReportVersion, "is_archived") and not include_archived:
        query = query.filter(ReportVersion.is_archived == False)

    reports = query.all()

    # ✅ Deduplicate per case
    latest_by_case = {}
    for report in reports:
        if report.case_id not in latest_by_case:
            latest_by_case[report.case_id] = report

    reports = list(latest_by_case.values())
    reports.sort(
        key=lambda r: (
            r.case.scan_datetime if getattr(r, "case", None) and r.case.scan_datetime else r.generated_at
        )
    )

    health_index_points = []
    weight_points = []
    systems = {}
    markers = {}

    # =========================
    # BUILD RAW SERIES
    # =========================
    for report in reports:
        snapshot = getattr(report, "metrics_snapshot", None) or {}
        marker_category_lookup = _marker_category_lookup_from_report(report)

        # Health index
        hi = snapshot.get("health_index")
        if isinstance(hi, (int, float)):
            health_index_points.append(_point(report, hi))

        # Weight (with fallback)
        weight = snapshot.get("weight_kg")
        if not isinstance(weight, (int, float)):
            source = getattr(report.case, "source_patient_data_json", None)
            if isinstance(source, dict):
                weight = source.get("weight_kg")

        if isinstance(weight, (int, float)):
            weight_points.append(_point(report, weight))

        # Systems
        for key, metric in (snapshot.get("systems") or {}).items():
            value = _metric_value(metric)
            if value is not None:
                systems.setdefault(_norm_key(key), []).append(
                    _point(report, value, _metric_meta(metric))
                )

        # Markers
        for key, metric in (snapshot.get("markers") or {}).items():
            value = _metric_value(metric)
            if value is not None:
                norm_key = _norm_key(key)
                fallback_category = marker_category_lookup.get(norm_key)
                if isinstance(metric, dict):
                    label_for_lookup = _metric_label(metric, key)
                    fallback_category = fallback_category or marker_category_lookup.get(_norm_key(label_for_lookup))

                markers.setdefault(norm_key, []).append(
                    _point(report, value, _metric_meta(metric, fallback_category, key))
                )

    # =========================
    # SYSTEM GROUPING (FIXED)
    # =========================
    system_groups = {}
    system_group_averages = {}

    system_key_lookup = {_norm_key(k): k for k in systems.keys()}

    for group_name, configured_keys in SYSTEM_GROUPS.items():
        available = []

        for configured_key in configured_keys:
            norm = _norm_key(configured_key)
            actual = system_key_lookup.get(norm)

            if actual:
                available.append({
                    "key": actual,
                    "label": _humanize_key(actual),
                })

        if available:
            system_groups[group_name] = available

    # fallback group
    if not system_groups and systems:
        system_groups["All systems"] = [
            {"key": k, "label": _humanize_key(k)}
            for k in sorted(systems.keys())
        ]

    # =========================
    # GROUP AVERAGES
    # =========================
    for group_name, options in system_groups.items():
        keys = [item["key"] for item in options]

        for report in reports:
            snapshot = getattr(report, "metrics_snapshot", None) or {}
            report_systems = snapshot.get("systems") or {}

            values = []
            for system_key, metric in report_systems.items():
                if _norm_key(system_key) in [_norm_key(k) for k in keys]:
                    value = _metric_value(metric)
                    if value is not None:
                        values.append(value)

            if values:
                system_group_averages.setdefault(group_name, []).append(
                    _point(report, sum(values) / len(values))
                )
    trend_summary = _compute_trend_summary(markers)
    trend_intelligence = _build_trend_intelligence(trend_summary, health_index_points, system_group_averages, report_count=len(reports))
    latest_ready_report = reports[-1] if reports else None
    trend_recommendations = _build_trend_recommendations(latest_ready_report, trend_summary)
    clinical_narrative = _build_clinical_narrative_summary(
        trend_summary=trend_summary,
        trend_intelligence=trend_intelligence,
        trend_recommendations=trend_recommendations,
        report_count=len(reports),
        health_index_points=health_index_points,
    )
    trend_intelligence["clinical_narrative"] = clinical_narrative
    trend_intelligence["top_clinical_priorities"] = clinical_narrative.get("top_priorities", [])
    trend_recommendations["clinical_priorities"] = clinical_narrative.get("top_priorities", [])

    return {
        "patient_id": str(patient.id),
        "health_index": health_index_points,
        "weight_kg": weight_points,
        "systems": systems,
        "markers": markers,
        "system_groups": system_groups,
        "system_group_averages": system_group_averages,
        "system_options": [{"key": k, "label": _humanize_key(k)} for k in sorted(systems.keys())],
        "marker_options": [{"key": k, "label": _humanize_key(k)} for k in sorted(markers.keys())],
        "trend_summary": trend_summary,
        "trend_intelligence": trend_intelligence,
        "trend_recommendations": trend_recommendations,
    }



