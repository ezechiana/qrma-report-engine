#app/schemas/share.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ShareLinkCreate(BaseModel):
    password: Optional[str] = None
    expires_at: Optional[datetime] = None


class ReportShareLinkCreate(ShareLinkCreate):
    expires_in_days: int = Field(default=7, ge=1)
    price_amount: int | None = Field(default=None, ge=0, le=99_999_999)
    price_currency: str = Field(default="gbp", pattern="^[A-Za-z]{3}$")


class ShareLinkRead(BaseModel):
    share_url: str
    token: str


class SharePasswordVerify(BaseModel):
    password: str