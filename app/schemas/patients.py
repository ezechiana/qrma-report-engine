from datetime import datetime, date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_required_names(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("This field is required.")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: Optional[date]) -> Optional[date]:
        if value is None:
            return value

        today = date.today()

        if value > today:
            raise ValueError("Date of birth cannot be in the future.")

        if value.year < 1900:
            raise ValueError("Please enter a realistic date of birth.")

        age = (
            today.year
            - value.year
            - ((today.month, today.day) < (value.month, value.day))
        )

        if age > 130:
            raise ValueError("Please enter a realistic date of birth.")

        return value

    @field_validator("sex")
    @classmethod
    def validate_sex(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None

        allowed = {"female", "male", "other", "prefer_not_to_say"}
        if value not in allowed:
            raise ValueError("Invalid sex option.")
        return value

    @field_validator("height_cm")
    @classmethod
    def validate_height(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0 or value > 300:
            raise ValueError("Please enter a realistic height in cm.")
        return value

    @field_validator("weight_kg")
    @classmethod
    def validate_weight(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0 or value > 500:
            raise ValueError("Please enter a realistic weight in kg.")
        return value


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_optional_names(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def validate_date_of_birth(cls, value: Optional[date]) -> Optional[date]:
        if value is None:
            return value

        today = date.today()

        if value > today:
            raise ValueError("Date of birth cannot be in the future.")

        if value.year < 1900:
            raise ValueError("Please enter a realistic date of birth.")

        age = (
            today.year
            - value.year
            - ((today.month, today.day) < (value.month, value.day))
        )

        if age > 130:
            raise ValueError("Please enter a realistic date of birth.")

        return value

    @field_validator("sex")
    @classmethod
    def validate_sex(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None

        allowed = {"female", "male", "other", "prefer_not_to_say"}
        if value not in allowed:
            raise ValueError("Invalid sex option.")
        return value

    @field_validator("height_cm")
    @classmethod
    def validate_height(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0 or value > 300:
            raise ValueError("Please enter a realistic height in cm.")
        return value

    @field_validator("weight_kg")
    @classmethod
    def validate_weight(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0 or value > 500:
            raise ValueError("Please enter a realistic weight in kg.")
        return value


class PatientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    full_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    case_count: int = 0
    report_count: int = 0
    last_activity_at: Optional[datetime] = None
    data_sources: list[str] = []