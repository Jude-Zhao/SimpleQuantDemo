"""Classification and constraints Pydantic schemas."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _ensure_finite(value: float | None) -> float | None:
    """BUG-16：权重/数值约束必须是有限值（inf/NaN 一律 422）。"""
    if value is not None and not math.isfinite(value):
        raise ValueError("约束数值必须是有限值（不允许 Infinity/NaN）")
    return value


class ClassificationRuleBase(BaseModel):
    rule_name: str
    category_key: str
    rule_type: str  # manual / by_field / by_range
    config: dict[str, Any] = {}
    is_active: bool = True
    priority: int = 100


class ClassificationRuleCreate(ClassificationRuleBase):
    pass


class ClassificationRuleUpdate(BaseModel):
    rule_name: str | None = None
    category_key: str | None = None
    rule_type: str | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None
    priority: int | None = None


class ClassificationRuleResponse(ClassificationRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class ClassificationResult(BaseModel):
    sec_code: str
    categories: dict[str, str]  # category_key -> category_value


class CategoryConstraint(BaseModel):
    category_key: str
    category_value: str
    min_weight: float | None = None
    max_weight: float | None = None
    min_count: int | None = None
    max_count: int | None = None

    @field_validator("min_weight", "max_weight")
    @classmethod
    def _finite_weights(cls, v: float | None) -> float | None:
        # min_count/max_count 的 int 类型本身即强制整数（1.5 → 422），无需额外校验。
        return _ensure_finite(v)


class OptimizationConstraints(BaseModel):
    single_min_weight: float | None = None
    single_max_weight: float | None = None
    category_constraints: list[CategoryConstraint] = []
    turnover_limit: float | None = None

    @field_validator("single_min_weight", "single_max_weight", "turnover_limit")
    @classmethod
    def _finite_values(cls, v: float | None) -> float | None:
        return _ensure_finite(v)

    # 注意：不校验 min<=max 等可行性——约束仅是提示模式，不可满足的配置也允许保存。


class ConstraintsSaveRequest(BaseModel):
    constraints: OptimizationConstraints
