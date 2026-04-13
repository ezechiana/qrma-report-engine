#app/schemas/patients.py

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PatientBase(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: Optional[date] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    created_at: datetime
    updated_at: datetime