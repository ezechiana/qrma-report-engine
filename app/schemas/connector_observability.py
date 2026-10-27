"""No identity, IP, paths or clinical data accepted from a heartbeat."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    connector_version: str = Field(min_length=1, max_length=64, pattern=r'^[0-9][0-9A-Za-z.+_-]*$')
    platform: Literal['windows']
