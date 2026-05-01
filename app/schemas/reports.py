from datetime import datetime
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GenerateReportResponse(BaseModel):
    report_version_id: UUID
    version_number: int
    status: str


class ReportVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    case_id: UUID
    version_number: int
    status: str
    build_version: Optional[str] = None
    recommendation_mode: str
    generated_at: datetime

    display_name: Optional[str] = None
    patient_display_name: Optional[str] = None
    case_title: Optional[str] = None
    scan_datetime: Optional[datetime] = None
    scan_datetime_display: Optional[str] = None
    is_archived: bool = False

    report_type: str = "assessment"
    source_report_ids: list[str] = []
    trend_options: dict[str, Any] = {}

    source_count: int = 0
    source_scan_range: Optional[str] = None
    health_index: Optional[float] = None
    health_index_change: Optional[float] = None


class ReportOverrideUpdate(BaseModel):
    practitioner_summary: Optional[str] = None
    follow_up_notes: Optional[str] = None
    clinical_recommendations_override_json: Optional[dict[str, Any] | list[Any]] = None
    support_plan_override_json: Optional[dict[str, Any]] = None
