"""Universe-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UniverseItemBase(BaseModel):
    sec_code: str
    sec_name: str
    meta: dict[str, Any] = {}


class UniverseItemCreate(UniverseItemBase):
    pass


class UniverseItemResponse(UniverseItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    added_at: datetime
    removed_at: datetime | None = None


class UniverseBatchAddRequest(BaseModel):
    items: list[UniverseItemCreate]
