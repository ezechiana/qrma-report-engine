"""Connector-only persistence; existing report/parser models remain unchanged."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Boolean, LargeBinary, JSON, UniqueConstraint, Uuid, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


def now():
    return datetime.now(timezone.utc)


class ConnectorDevice(Base):
    __tablename__ = 'connector_devices'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    connector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)


class ConnectorPairing(Base):
    __tablename__ = 'connector_pairings'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    challenge: Mapped[str] = mapped_column(String(64))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class IncomingAssessment(Base):
    __tablename__ = 'connector_assessments'
    __table_args__ = (UniqueConstraint('owner_id', 'connector_id', 'job_id', name='uq_connector_job'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    connector_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_profile: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    raw_sha256: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source_html: Mapped[bytes] = mapped_column(LargeBinary)
    parsed_metadata: Mapped[dict] = mapped_column(JSON)
    duplicate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(40), default='awaiting_practitioner')
    patient_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("patients.id"), nullable=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("cases.id"), nullable=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("report_versions.id"), nullable=True)
