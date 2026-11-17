#app/schemas/share.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ShareLinkCreate(BaseModel):
    password: Optional[str] = None
    expires_at: Optional[datetime] = None


class ReportShareLinkCreate(ShareLinkCreate):
    expires_in_days: int = Field(default=7, ge=1)


class ShareLinkRead(BaseModel):
    share_url: str
    token: str


class SharePasswordVerify(BaseModel):
    password: str