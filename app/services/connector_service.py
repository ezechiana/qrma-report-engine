"""Bounded ingestion and database-backed transport identity."""
import hashlib
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import or_, select, func
from sqlalchemy.exc import IntegrityError
from app.db.connector_models import IncomingAssessment
from app.services import connector_audit as audit, connector_observability as telemetry

MAX_HTML = 8 * 1024 * 1024
PROFILE = 'go360-wellness-intelligence-v1'


def validate_html(raw):
    if not raw or len(raw) > MAX_HTML:
        raise HTTPException(413, 'Assessment must be between 1 byte and 8 MiB.')
    if b'\x00' in raw or b'<html' not in raw[:65536].lower():
        raise HTTPException(422, 'Expected QRMA HTML.')
    from bs4 import UnicodeDammit
    from app.parser.html_parser import parse_html_report
    from app.services.assessment_fingerprint import fingerprint_html
    decoded = UnicodeDammit(raw, is_html=True).unicode_markup
    try:
        report = parse_html_report(decoded or '')
    except Exception:
        raise HTTPException(422, 'Assessment could not be parsed.') from None
    rows = sum(len(section.parameters) for section in report.sections)
    if not rows or rows > 10000 or len(report.sections) > 200:
        raise HTTPException(422, 'Unsupported assessment structure.')
    from app.services.scan_import_service import parse_qrma_html
    metadata = parse_qrma_html(raw)
    # Authoritative parser supplies source identity; existing import parser supplies
    # matching-form shape. Never substitute current patient-record identity.
    p = report.patient
    target = metadata.setdefault('source_patient_data', {})
    for key in ('full_name', 'sex', 'age', 'height_cm', 'weight_kg'):
        target[key] = getattr(p, key, None)
    if target.get('full_name'):
        parts = target['full_name'].split(maxsplit=1)
        target['first_name'] = parts[0]
        target['last_name'] = parts[1] if len(parts) > 1 else ''
    return metadata, fingerprint_html(raw)


def json_safe(value):
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value.isoformat() if isinstance(value, datetime) else value


def result(row, include_metadata=False):
    value = dict(assessment_id=str(row.id), connector_id=str(row.connector_id),
                 job_id=str(row.job_id), status=row.state,
                 received_at=row.received_at.isoformat(),
                 source_profile=row.source_profile, duplicate=row.duplicate,
                 patient_id=str(row.patient_id) if row.patient_id else None,
                 case_id=str(row.case_id) if row.case_id else None,
                 report_version_id=str(row.report_id) if row.report_id else None,
                 review_path='/app/cases/new?incoming_assessment_id=' + str(row.id))
    if include_metadata:
        value.update(row.parsed_metadata)
    return value


def owned(db, owner, assessment_id, lock=False):
    query = select(IncomingAssessment).where(IncomingAssessment.id == assessment_id,
                                            IncomingAssessment.owner_id == owner)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, 'Assessment not found.')
    return row


def duplicate_match(db, owner, digest, fingerprint, exclude=None):
    from app.db.models import Case
    tests = [Case.raw_scan_hash == digest]
    if fingerprint:
        tests.append(Case.assessment_fingerprint == fingerprint)
    case = db.scalar(select(Case).where(Case.user_id == owner, or_(*tests)).order_by(Case.created_at, Case.id))
    if case:
        from app.db.models import ReportVersion
        report = db.scalar(select(ReportVersion).where(ReportVersion.case_id == case.id)
                           .order_by(ReportVersion.version_number.desc()))
        return dict(code='duplicate_scan', match_type='exact_file' if case.raw_scan_hash == digest else 'assessment',
                    existing_case_id=str(case.id), existing_report_id=str(report.id) if report else None,
                    existing_assessment_id=None, allow_duplicate_supported=True,
                    message='This assessment already exists. Review it or explicitly import anyway.')
    tests = [IncomingAssessment.raw_sha256 == digest]
    if fingerprint:
        tests.append(IncomingAssessment.fingerprint == fingerprint)
    query = select(IncomingAssessment).where(IncomingAssessment.owner_id == owner, or_(*tests))
    if exclude:
        query = query.where(IncomingAssessment.id != exclude)
    other = db.scalar(query.order_by(IncomingAssessment.received_at, IncomingAssessment.id))
    if other:
        return dict(code='duplicate_scan', match_type='exact_file' if other.raw_sha256 == digest else 'assessment',
                    existing_case_id=str(other.case_id) if other.case_id else None,
                    existing_report_id=str(other.report_id) if other.report_id else None,
                    existing_assessment_id=str(other.id), allow_duplicate_supported=True,
                    message='An equivalent incoming assessment exists. Review it or explicitly import anyway.')
    return None


def ingest(db, device, job_id, profile, captured_at, raw, observation=None):
    if profile != PROFILE:
        raise HTTPException(422, 'Source profile has not been validated.')
    digest = hashlib.sha256(raw).hexdigest()
    identity = (IncomingAssessment.owner_id == device.owner_id,
                IncomingAssessment.connector_id == device.id, IncomingAssessment.job_id == job_id)
    existing = db.scalar(select(IncomingAssessment).where(*identity))
    if existing:
        if existing.raw_sha256 != digest:
            raise HTTPException(409, 'Job ID content conflict.')
        if audit.enabled():
            telemetry.touch(db, device, observation)
            db.commit()
        return result(existing)
    metadata, fingerprint = validate_html(raw)
    # Serialize duplicates for this account, including two different simultaneous
    # jobs for the same assessment. Row-level owner lock precedes duplicate query.
    from app.db.models import User
    db.scalar(select(User).where(User.id == device.owner_id).with_for_update())
    pending_count = db.scalar(select(func.count()).select_from(IncomingAssessment).where(
        IncomingAssessment.owner_id == device.owner_id, IncomingAssessment.case_id.is_(None)))
    if pending_count >= 500:
        raise HTTPException(429, 'Review incoming assessments before sending more.')
    duplicate = duplicate_match(db, device.owner_id, digest, fingerprint)
    row = IncomingAssessment(owner_id=device.owner_id, connector_id=device.id, job_id=job_id,
          source_profile=profile, captured_at=captured_at, raw_sha256=digest,
          fingerprint=fingerprint, source_html=raw, parsed_metadata=json_safe(metadata),
          duplicate=duplicate, state='needs_attention' if duplicate else 'awaiting_practitioner')
    db.add(row)
    try:
        # Flush row identity before event correlation; same transaction.
        db.flush()
        telemetry.touch(db, device, observation)
        audit.assessment_event(db, 'assessment_received', row, observation=observation)
        if duplicate:
            audit.assessment_event(db, 'duplicate_held', row, observation=observation)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(IncomingAssessment).where(*identity))
        if existing is None:
            raise
        if existing.raw_sha256 != digest:
            raise HTTPException(409, 'Job ID content conflict.')
        return result(existing)
    return result(row)


def reconcile(db, row, *, device=None, observation=None):
    if audit.enabled():
        row = owned(db, row.owner_id, row.id, lock=True)
    before = (row.report_id, row.state)
    if row.case_id:
        from app.db.models import ReportVersion
        report = db.scalar(select(ReportVersion).where(ReportVersion.case_id == row.case_id)
                           .order_by(ReportVersion.version_number.desc()))
        if report:
            row.report_id = report.id
            status = report.status.value if hasattr(report.status, 'value') else str(report.status)
            row.state = 'completed' if status in {'ready','final'} else 'needs_attention' if status == 'failed' else 'processing'
    if audit.enabled():
        if before != (row.report_id, row.state):
            # Row lock makes repeated polls emit one event per real transition.
            audit.record(db, 'report_state_changed', actor_id=row.owner_id, owner_id=row.owner_id,
                         connector_id=row.connector_id, assessment=row, observation=observation)
        if device is not None:
            telemetry.touch(db, device, observation)
        db.commit()
    elif row.case_id:
        db.commit()
    return result(row)
