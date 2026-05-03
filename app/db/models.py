#app/db/models.py
from ast import Index
import enum
import uuid
from datetime import date, datetime
from typing import Optional
import json
from sqlalchemy import (
    Boolean,
    JSON,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Index
from app.db.base import Base


class CaseStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    processing = "processing"
    generated = "generated"
    final = "final"
    failed = "failed"
    archived = "archived"


class ReportStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    final = "final"


class RecommendationMode(str, enum.Enum):
    recommendations_off = "recommendations_off"
    affiliate_vitalhealth = "affiliate_vitalhealth"
    vitalhealth_clinical_optimised = "vitalhealth_clinical_optimised"
    natural_approaches_clinical = "natural_approaches_clinical"
    mixed_clinical = "mixed_clinical"

class PractitionerSettings(Base):
    __tablename__ = "practitioner_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    clinic_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    report_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    report_subtitle: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    logo_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    cover_image_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    accent_color: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    support_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Dashboard performance settings. Actual revenue remains reported in the
    # original transaction currencies; these fields are only used for converted
    # goal tracking and visual dashboard progress.
    preferred_currency: Mapped[Optional[str]] = mapped_column(String(10), default="USD", nullable=True)
    monthly_goal_minor: Mapped[Optional[int]] = mapped_column(Integer, default=200000, nullable=True)

    recommendation_mode_default: Mapped[RecommendationMode] = mapped_column(
        Enum(RecommendationMode),
        default=RecommendationMode.natural_approaches_clinical,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="settings")

    
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    clinic_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    patients: Mapped[list["Patient"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    cases: Mapped[list["Case"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    settings: Mapped[Optional["PractitionerSettings"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    recommendation_mode_default: Mapped[RecommendationMode] = mapped_column(
        Enum(RecommendationMode),
        default=RecommendationMode.natural_approaches_clinical,
    )

    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    primary_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    accent_color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    support_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/London")
    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    feedback_items: Mapped[list["FeedbackItem"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)

    first_name: Mapped[str] = mapped_column(String(120))
    last_name: Mapped[str] = mapped_column(String(120))
    full_name: Mapped[str] = mapped_column(String(255), index=True)

    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sex: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    height_cm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="patients")
    cases: Mapped[list["Case"]] = relationship(back_populates="patient", cascade="all, delete-orphan")


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), index=True)
    raw_scan_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.draft, index=True)
    recommendation_mode: Mapped[RecommendationMode] = mapped_column(
        Enum(RecommendationMode),
        default=RecommendationMode.natural_approaches_clinical,
    )

    clinical_context_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    source_patient_data_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    raw_scan_html_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    scan_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="cases")
    patient: Mapped["Patient"] = relationship(back_populates="cases")
    report_versions: Mapped[list["ReportVersion"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class ReportVersion(Base):
    __tablename__ = "report_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    version_number: Mapped[int] = mapped_column(Integer)

    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus),
        default=ReportStatus.queued,
        index=True,
    )
    
    recommendation_mode: Mapped[RecommendationMode] = mapped_column(Enum(RecommendationMode))

    # nullable until build completes
    report_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    html_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    build_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    trend_payload_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    report_type: Mapped[str] = mapped_column(String(30), default="assessment", nullable=False)
    source_report_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    trend_options: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)


    # execution lifecycle
    job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    metrics_snapshot = Column(JSON, nullable=True)

    case: Mapped["Case"] = relationship(back_populates="report_versions")
    overrides: Mapped[Optional["ReportOverride"]] = relationship(
        back_populates="report_version",
        cascade="all, delete-orphan",
        uselist=False,
    )
    share_links: Mapped[list["ShareLink"]] = relationship(
        back_populates="report_version",
        cascade="all, delete-orphan",
    )
    feedback_items: Mapped[list["FeedbackItem"]] = relationship(
        back_populates="report_version",
        cascade="all, delete-orphan",
    )


class ReportOverride(Base):
    __tablename__ = "report_overrides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_versions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    practitioner_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    follow_up_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clinical_recommendations_override_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    support_plan_override_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    report_version: Mapped["ReportVersion"] = relationship(back_populates="overrides")


class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_versions.id", ondelete="CASCADE"),
        index=True,
    )

    token: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    report_version: Mapped["ReportVersion"] = relationship(back_populates="share_links")
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    share_type: Mapped[str] = mapped_column(String(30), default="report_bundle", nullable=False)
    bundle_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    price_pence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ShareLinkItem(Base):
    __tablename__ = "share_link_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("share_links.id", ondelete="CASCADE"),
        index=True,
    )
    report_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_versions.id", ondelete="CASCADE"),
        index=True,
    )
    item_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())




class SubscriptionStatus(str, enum.Enum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    incomplete = "incomplete"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    plan_code: Mapped[str] = mapped_column(String(100))
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus),
        default=SubscriptionStatus.incomplete,
        index=True,
    )
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    case_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True)
    report_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[str] = mapped_column(String(120), index=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class FeedbackSentiment(str, enum.Enum):
    positive = "positive"
    negative = "negative"
    neutral = "neutral"


    
class FeedbackItem(Base):
    __tablename__ = "feedback_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    report_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("report_versions.id", ondelete="CASCADE"),
        index=True,
    )

    section_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    marker_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sentiment: Mapped[FeedbackSentiment] = mapped_column(Enum(FeedbackSentiment), index=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="feedback_items")
    report_version: Mapped["ReportVersion"] = relationship(back_populates="feedback_items")


class ShareBundle(Base):
    __tablename__ = "share_bundles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    patient_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=True, index=True)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    access_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    requires_payment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_status: Mapped[str] = mapped_column(String(40), default="not_required", nullable=False)
    price_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    price_currency: Mapped[str] = mapped_column(String(10), default="gbp", nullable=False)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items: Mapped[list["ShareBundleItem"]] = relationship(
        back_populates="bundle",
        cascade="all, delete-orphan",
        order_by="ShareBundleItem.position",
    )


class ShareBundleItem(Base):
    __tablename__ = "share_bundle_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    share_bundle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("share_bundles.id", ondelete="CASCADE"), index=True)
    report_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("report_versions.id", ondelete="CASCADE"), index=True)
    share_link_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("share_links.id", ondelete="CASCADE"), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bundle: Mapped["ShareBundle"] = relationship(back_populates="items")
    report_version: Mapped["ReportVersion"] = relationship()
    share_link: Mapped[Optional["ShareLink"]] = relationship()
