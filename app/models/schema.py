from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict


class Patient(BaseModel):
    full_name: Optional[str] = None
    sex: Optional[str] = None
    age: Optional[int] = None
    date_of_birth: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None


class ProductRecommendation(BaseModel):
    code: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    patient_note: Optional[str] = None
    url: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class ParameterResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_name: str
    normal_range_text: Optional[str] = ""
    actual_value_text: Optional[str] = ""
    actual_value_numeric: Optional[float] = None
    result_image_code: Optional[str] = None

    status: Optional[str] = None
    severity: Optional[str] = None
    is_abnormal: Optional[bool] = False
    severity_rank: Optional[int] = 0
    marker_priority: Optional[str] = None

    control_id: Optional[str] = None
    canonical_system: Optional[str] = None
    clinical_label: Optional[str] = None
    original_report_category: Optional[str] = None
    display_label: Optional[str] = None
    pattern_cluster: Optional[str] = None

    what_it_means: Optional[str] = None
    why_it_matters: Optional[str] = None
    functional_significance: Optional[str] = None
    common_patterns: Optional[str] = None
    patient_interpretation: Optional[str] = None
    clinical_relevance_score: Optional[int] = None

    product_tags: List[str] = Field(default_factory=list)
    recommend_if_low: Optional[str] = None
    recommend_if_high: Optional[str] = None
    recommendation_notes: Optional[str] = None

    @property
    def display_name(self) -> str:
        return self.display_label or self.clinical_label or self.source_name

    @property
    def display_value(self) -> str:
        if self.actual_value_text:
            return self.actual_value_text
        if self.actual_value_numeric is not None:
            return str(self.actual_value_numeric)
        return ""

    @property
    def display_range(self) -> str:
        return self.normal_range_text or ""
    
    @property
    def name(self) -> str:
        return self.source_name


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_title: str
    display_title: Optional[str] = None
    parameters: List[ParameterResult] = Field(default_factory=list)

    abnormal_count: int = 0
    normal_count: int = 0
    section_score: int = 0
    priority: str = "normal"
    summary: Optional[str] = None
    top_findings: List[str] = Field(default_factory=list)
    interpretation: Optional[str] = None

    original_report_category: Optional[str] = None
    canonical_system: Optional[str] = None

    @property
    def title(self) -> str:
        return self.display_title or self.source_title


class DetectedPattern(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    strength: str
    confidence: float
    description: str
    systems: List[str] = Field(default_factory=list)
    supporting_markers: List[str] = Field(default_factory=list)
    priority: str = "medium"


class NarrativeBlock(BaseModel):
    model_config = ConfigDict(extra="allow")

    opening_summary: str = ""
    core_story: str = ""
    root_cause_flow: str = ""
    priority_focus: str = ""
    closing_summary: str = ""


class PractitionerNotes(BaseModel):
    practitioner_summary: Optional[str] = None
    recommendations: List[str] = Field(default_factory=list)
    follow_up_suggestions: List[str] = Field(default_factory=list)
    disclaimer_note: Optional[str] = None


class ReportOverrides(BaseModel):
    practitioner_notes: PractitionerNotes = Field(default_factory=PractitionerNotes)
    section_summary_overrides: Dict[str, str] = Field(default_factory=dict)
    marker_comment_overrides: Dict[str, str] = Field(default_factory=dict)


class ParsedReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    patient: Patient
    report_profile: Optional[str] = None
    sections: List[ReportSection] = Field(default_factory=list)
    overall_summary: Optional[str] = None
    priority_sections: List[str] = Field(default_factory=list)
    patterns: List[DetectedPattern] = Field(default_factory=list)
    narrative: Optional[NarrativeBlock] = None
    product_recommendations: List[Dict[str, Any]] = Field(default_factory=list)
    practitioner_summary: Optional[str] = None
    key_patterns: List[str] = Field(default_factory=list)
    priority_actions: List[str] = Field(default_factory=list)

class ReportBuildRequest(BaseModel):
    report_data: ParsedReport
    overrides: ReportOverrides = Field(default_factory=ReportOverrides)
