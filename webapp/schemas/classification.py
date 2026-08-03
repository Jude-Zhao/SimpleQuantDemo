"""Classification and constraints Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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


class OptimizationConstraints(BaseModel):
    single_min_weight: float | None = None
    single_max_weight: float | None = None
    category_constraints: list[CategoryConstraint] = []
    turnover_limit: float | None = None


class ConstraintsSaveRequest(BaseModel):
    constraints: OptimizationConstraints
