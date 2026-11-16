"""Accepting a clinic invite links records and leaves releases and share links alone."""

import os
import uuid
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/qrma_import_check")

from sqlalchemy import Uuid, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.db.models import (
    Case,
    ClinicAssociation,
    ContentRelease,
    Patient,
    PatientClaimInvite,
    PatientSource,
    ReportStatus,
    ReportVersion,
    ShareLink,
    SourceObservation,
    SourceRecord,
    User,
)
from app.services.clinic_association_service import (
    ACTIVE,
    backfill_legacy_claimed_chart,
)
from app.services.patient_claim_service import (
    begin_client_claim,
    complete_reserved_claim,
    create_claim_invite,
)
from app.services.patient_source_service import (
    CLINIC_TO_CLIENT,
    create_clinic_note,
    ensure_manual_source,
    list_patient_sources,
    record_source_key,
)
from app.services.personal_record_service import clinic_charts, ensure_personal_record
from app.services.personal_source_service import add_personal_note, list_personal_notes


class FlexibleUuid(TypeDecorator):
    impl = Uuid
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return uuid.UUID(value) if isinstance(value, str) else value


@compiles(JSONB, "sqlite")
def sqlite_json(type_, compiler, **kw):
    return "JSON"


MODELS = (
    User,
    Patient,
    PatientClaimInvite,
    ClinicAssociation,
    Case,
    ReportVersion,
    ShareLink,
    ContentRelease,
    PatientSource,
    SourceRecord,
    SourceObservation,
)


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    originals = []
    for model in MODELS:
        for column in model.__table__.columns:
            if isinstance(column.type, Uuid):
                originals.append((column, column.type))
                column.type = FlexibleUuid()
    for model in MODELS:
        model.__table__.create(engine)
    return engine, originals, Session(engine)


def _restore(engine, originals):
    for column, kind in originals:
        column.type = kind
    engine.dispose()


def _user(email, role, name):
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        full_name=name,
        role=role,
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )


def _share(db, practitioner, patient, token):
    case = Case(
        id=uuid.uuid4(),
        user_id=practitioner.id,
        patient_id=patient.id,
        title="Morning scan",
        status="draft",
    )
    db.add(case)
    db.commit()
    report = ReportVersion(
        id=uuid.uuid4(),
        case_id=case.id,
        created_by_user_id=practitioner.id,
        version_number=1,
        status=ReportStatus.ready,
        recommendation_mode="natural_approaches_clinical",
        report_type="assessment",
        is_archived=False,
    )
    db.add(report)
    db.commit()
    link = ShareLink(
        id=uuid.uuid4(),
        report_version_id=report.id,
        token=token,
        is_active=True,
        share_type="report",
        patient_id=patient.id,
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(link)
    db.commit()
    return link


def test_accepting_an_invite_links_the_personal_record_without_releasing(monkeypatch):
    monkeypatch.setattr(
        "app.services.patient_claim_service.send_email",
        lambda **kwargs: {"sent": True},
    )
    monkeypatch.setattr(
        "app.services.auth_service.create_email_verification_token",
        lambda db, user, expires_hours=24: "verify-token",
    )
    monkeypatch.setattr(
        "app.services.patient_claim_service.send_verification_email",
        lambda **kwargs: {"sent": True},
    )
    engine, originals, db = _session()
    try:
        practitioner = _user("clinic@example.com", "practitioner", "Clinic Owner")
        practitioner.clinic_name = "North Clinic"
        clinic = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        db.add_all([practitioner, clinic])
        db.commit()
        link = _share(db, practitioner, clinic, "kept-invite-share")

        invite, _url, sent = create_claim_invite(db, practitioner, clinic.id, "ada@example.com")
        assert sent is True
        assert db.query(ClinicAssociation).count() == 0
        assert db.query(ContentRelease).count() == 0

        account, _token = begin_client_claim(
            db,
            raw_token=_url.rsplit("/", 1)[-1],
            email="ada@example.com",
            password="ClaimPass1x",
            confirm_password="ClaimPass1x",
            consent=True,
        )
        personal = db.query(Patient).filter(Patient.subject_user_id == account.id).one()
        assert personal.id != clinic.id
        add_personal_note(
            db,
            personal,
            account,
            title="Personal shelf",
            recorded_on=date(2026, 11, 16),
            text="Personal shelf note 44111",
        )
        source = ensure_manual_source(db, clinic)
        clinic_note = create_clinic_note(
            db,
            clinic,
            source.id,
            title="Clinic note",
            recorded_on=date(2026, 11, 16),
            text="Clinic private note 44110",
            client_summary="",
            created_by_user_id=practitioner.id,
        )
        assert db.query(ContentRelease).count() == 0

        account.email_verified_at = datetime.now(timezone.utc)
        claimed = complete_reserved_claim(db, account)
        db.commit()
        assert claimed is not None
        assert claimed.id == invite.id

        db.refresh(clinic)
        db.refresh(personal)
        db.refresh(link)
        assert clinic.user_id == practitioner.id
        assert clinic.claimed_by_user_id == account.id
        assert clinic.subject_user_id is None
        assert personal.subject_user_id == account.id
        assert personal.claimed_by_user_id is None
        assert personal.user_id == account.id

        association = db.query(ClinicAssociation).one()
        assert association.status == ACTIVE
        assert association.initiated_by == "practitioner"
        assert association.personal_patient_id == personal.id
        assert association.clinic_patient_id == clinic.id
        assert association.practitioner_user_id == practitioner.id
        assert association.activated_at is not None

        assert db.query(ContentRelease).count() == 0
        assert link.token == "kept-invite-share"
        assert link.is_active is True
        personal_notes = list_personal_notes(db, personal, account)
        assert personal_notes["notes"][0]["text"] == "Personal shelf note 44111"
        assert personal_notes["notes"][0]["released"] is False
        clinic_sources = list_patient_sources(db, clinic)
        clinic_text = str(clinic_sources)
        assert "Clinic private note 44110" in clinic_text
        assert "Personal shelf note 44111" not in clinic_text
        assert clinic_note["record_id"] in clinic_text
        manual = next(group for group in clinic_sources["sources"] if group["source_type"] == "manual")
        assert manual["records"][0]["released"] is False
        own_sources = list_patient_sources(db, personal)
        assert "Personal shelf note 44111" not in str(own_sources)

        listed = clinic_charts(db, practitioner)
        assert [row.id for row in listed] == [clinic.id]
        stranger = _user("other@example.com", "client", "Other Person")
        db.add(stranger)
        db.commit()
        assert complete_reserved_claim(db, stranger) is None
        assert db.query(ClinicAssociation).count() == 1
        db.close()
    finally:
        _restore(engine, originals)


def test_legacy_claimed_chart_stays_one_row_and_is_not_released():
    engine, originals, db = _session()
    try:
        practitioner = _user("clinic@example.com", "practitioner", "Clinic Owner")
        client = _user("ada@example.com", "client", "Ada Lovelace")
        clinic = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
            claimed_by_user_id=client.id,
            claimed_at=datetime.now(timezone.utc),
        )
        db.add_all([practitioner, client, clinic])
        db.commit()
        link = _share(db, practitioner, clinic, "kept-legacy-share")
        source = PatientSource(
            id=uuid.uuid4(),
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_type="wearable",
            display_name="Wearables",
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        record = SourceRecord(
            id=uuid.uuid4(),
            patient_source_id=source.id,
            patient_id=clinic.id,
            recorded_at=datetime.now(timezone.utc),
            title="Day",
            status="ready",
            payload_json={"kind": "wearable_day"},
            is_archived=False,
            created_by_user_id=client.id,
            created_at=datetime.now(timezone.utc),
        )
        observation = SourceObservation(
            id=uuid.uuid4(),
            source_record_id=record.id,
            patient_id=clinic.id,
            position=1,
            name="Steps",
            value_numeric=86111,
            unit="steps",
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([source, record, observation])
        db.commit()
        note_source = ensure_manual_source(db, clinic)
        create_clinic_note(
            db,
            clinic,
            note_source.id,
            title="Clinic note",
            recorded_on=date(2026, 11, 16),
            text="Legacy clinic note 44112",
            client_summary="",
            created_by_user_id=practitioner.id,
        )

        linked = backfill_legacy_claimed_chart(db, clinic)
        db.commit()
        again = backfill_legacy_claimed_chart(db, clinic)
        db.commit()

        db.refresh(clinic)
        db.refresh(link)
        db.refresh(observation)
        assert clinic.subject_user_id == client.id
        assert clinic.user_id == practitioner.id
        assert clinic.claimed_by_user_id == client.id
        assert linked is not None
        assert again.id == linked.id
        assert linked.personal_patient_id == clinic.id
        assert linked.clinic_patient_id == clinic.id
        assert linked.practitioner_user_id == practitioner.id
        assert linked.status == ACTIVE
        assert db.query(ClinicAssociation).count() == 1
        assert db.query(Patient).count() == 1
        assert observation.patient_id == clinic.id
        assert int(observation.value_numeric) == 86111
        assert link.token == "kept-legacy-share"
        assert link.is_active is True
        assert db.query(ContentRelease).count() == 0
        sources = list_patient_sources(db, clinic)
        assert "Legacy clinic note 44112" in str(sources)
        manual = next(group for group in sources["sources"] if group["source_type"] == "manual")
        assert manual["records"][0]["released"] is False
        assert [row.id for row in clinic_charts(db, practitioner)] == [clinic.id]
        db.close()
    finally:
        _restore(engine, originals)


def test_accepting_again_does_not_restore_a_withdrawn_release():
    engine, originals, db = _session()
    try:
        practitioner = _user("clinic@example.com", "practitioner", "Clinic Owner")
        client = _user("ada@example.com", "client", "Ada Lovelace")
        client.role = "client"
        clinic = Patient(
            id=uuid.uuid4(),
            user_id=practitioner.id,
            first_name="Ada",
            last_name="Lovelace",
            full_name="Ada Lovelace",
        )
        db.add_all([practitioner, client, clinic])
        db.commit()
        personal = ensure_personal_record(db, client, commit=True)
        source = ensure_manual_source(db, clinic)
        note = create_clinic_note(
            db,
            clinic,
            source.id,
            title="Clinic note",
            recorded_on=date(2026, 11, 16),
            text="Withdrawn clinic note 44113",
            client_summary="Summary the client could see",
            created_by_user_id=practitioner.id,
        )
        withdrawn_at = datetime.now(timezone.utc) - timedelta(days=1)
        release = ContentRelease(
            id=uuid.uuid4(),
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            source_key=record_source_key(uuid.UUID(note["record_id"])),
            direction=CLINIC_TO_CLIENT,
            released=False,
            withdrawn_at=withdrawn_at,
            created_at=withdrawn_at,
        )
        ended = ClinicAssociation(
            id=uuid.uuid4(),
            personal_patient_id=personal.id,
            clinic_patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            status="ended",
            initiated_by="practitioner",
            created_at=withdrawn_at,
            activated_at=withdrawn_at,
            ended_at=withdrawn_at,
            ended_by="person",
        )
        invite = PatientClaimInvite(
            id=uuid.uuid4(),
            patient_id=clinic.id,
            practitioner_user_id=practitioner.id,
            email=client.email,
            token_hash="reserved-hash",
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(days=2),
            created_at=datetime.now(timezone.utc),
            reserved_by_user_id=client.id,
            reserved_at=datetime.now(timezone.utc),
        )
        db.add_all([release, ended, invite])
        db.commit()

        claimed = complete_reserved_claim(db, client)
        db.commit()
        assert claimed is not None
        db.refresh(ended)
        db.refresh(release)
        db.refresh(clinic)
        assert ended.status == ACTIVE
        assert ended.ended_at is None
        assert ended.ended_by is None
        assert db.query(ClinicAssociation).count() == 1
        assert release.released is False
        assert release.withdrawn_at is not None
        assert db.query(ContentRelease).count() == 1
        sources = list_patient_sources(db, clinic)
        manual = next(group for group in sources["sources"] if group["source_type"] == "manual")
        assert manual["records"][0]["released"] is False
        assert clinic.claimed_by_user_id == client.id
        assert personal.id != clinic.id
        db.close()
    finally:
        _restore(engine, originals)
