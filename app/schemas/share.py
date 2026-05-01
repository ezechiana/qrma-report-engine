#app/schemas/share.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ShareLinkCreate(BaseModel):
    password: Optional[str] = None
    expires_at: Optional[datetime] = None


class ShareLinkRead(BaseModel):
    share_url: str
    token: str


class SharePasswordVerify(BaseModel):
    password: str