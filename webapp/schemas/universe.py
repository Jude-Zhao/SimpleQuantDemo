"""Universe-related Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# BUG-17：新增标的时强制六位数字 + .SH/.SZ 后缀（与现有 ETF 范围校验一致）。
# 只约束创建入口（UniverseItemCreate）；响应模型（Base）保留 str，
# 历史异常代码 GET/展示不受影响（只报告不自动删除）。
SEC_CODE_PATTERN = r"^[0-9]{6}\.(SH|SZ)$"


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
    # 422 整批拒绝：混合合法/非法批次在 Pydantic 入口即失败，不会写一半。
    sec_code: str = Field(pattern=SEC_CODE_PATTERN)


class UniverseItemResponse(UniverseItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    added_at: datetime
    removed_at: datetime | None = None


class UniverseBatchAddRequest(BaseModel):
    items: list[UniverseItemCreate]
