"""Universe-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UniverseItemBase(BaseModel):
    sec_code: str
    sec_name: str
    meta: dict[str, Any] = {}
    # Optional per-item classification: {category_key: category_value}
    # (e.g. {"asset_type": "宽基", "style": "均衡"}). When provided on add,
    # the matching manual rules are auto-upserted so the item is classified
    # immediately via the rule engine (single source of truth).
    classification: dict[str, str] = {}


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
