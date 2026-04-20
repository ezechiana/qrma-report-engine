from datetime import datetime, date
from typing import Optional, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClinicalContext(BaseModel):
    conditions: list[str] = []
    symptoms: list[str] = []
    goals: list[str] = []
    contraindications: list[str] = []
    current_supplements: list[str] = []
    priority_focus: list[str] = []
    notes: Optional[str] = None
    custom_recommendations: list[dict[str, Any]] = []


class CaseCreate(BaseModel):
    patient_id: UUID
    title: str
    recommendation_mode: str = "natural_approaches_clinical"
    clinical_context_json: Optional[ClinicalContext] = None


class CaseCreateFromImport(BaseModel):
    title: str
    recommendation_mode: str = "natural_approaches_clinical"
    clinical_context_json: Optional[ClinicalContext] = None


class CaseUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    recommendation_mode: Optional[str] = None
    clinical_context_json: Optional[ClinicalContext] = None


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    title: str
    display_name: Optional[str] = None
    patient_display_name: Optional[str] = None
    status: str
    recommendation_mode: str
    clinical_context_json: Optional[dict] = None
    source_patient_data_json: Optional[dict] = None
    raw_scan_html_path: Optional[str] = None
    scan_datetime: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ParsedSourcePatientData(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None


class ParsedScanMetadata(BaseModel):
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    scan_datetime: Optional[datetime] = None


class ImportFromHtmlResponse(BaseModel):
    source_patient_data: ParsedSourcePatientData
    scan_metadata: ParsedScanMetadata
    temporary_upload_id: str


class CreateFromImportRequest(BaseModel):
    temporary_upload_id: str
    patient: ParsedSourcePatientData
    case: CaseCreateFromImport




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


class ReportOverrideUpdate(BaseModel):
    practitioner_summary: Optional[str] = None
    follow_up_notes: Optional[str] = None
    clinical_recommendations_override_json: Optional[dict[str, Any] | list[Any]] = None
    support_plan_override_json: Optional[dict[str, Any]] = None