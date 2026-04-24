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
    }



