"""Classification and constraints Pydantic schemas."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator


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


RULE_TYPES = ("manual", "by_field", "by_range")


def _check_rule_type(value: str) -> str:
    if value not in RULE_TYPES:
        raise ValueError(f"rule_type 必须为 {'/'.join(RULE_TYPES)} 之一，收到 {value!r}")
    return value


class ClassificationRuleCreate(ClassificationRuleBase):
    @field_validator("rule_type")
    @classmethod
    def _rule_type_valid(cls, value: str) -> str:
        return _check_rule_type(value)


class ClassificationRuleUpdate(BaseModel):
    rule_name: str | None = None
    category_key: str | None = None
    rule_type: str | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None
    priority: int | None = None

    # F14: field_validator 仅在字段被提交时触发——区分"未提交"（保持原值）
    # 与"提交 null"（拒绝）。非空列（rule_name/category_key/rule_type/config）
    # 一旦被显式置 null，轻则 500、重则 config=null 持久化后所有分类查询崩溃。
    @field_validator("rule_name", "category_key")
    @classmethod
    def _text_field_not_null(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            raise ValueError(f"{info.field_name} 不能为 null；未提交则保持原值，请省略该字段")
        return value

    @field_validator("rule_type")
    @classmethod
    def _rule_type_valid(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("rule_type 不能为 null；未提交则保持原值，请省略该字段")
        return _check_rule_type(value)

    @field_validator("config")
    @classmethod
    def _config_not_null(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            raise ValueError(
                "config 不能为 null：未提交则保持原值，修改请提交完整配置字典"
            )
        return value


class ClassificationRuleResponse(ClassificationRuleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    # F14: 遗留损坏记录的 config 可能为 null——读模型容忍以便管理页可访问，
    # 写模型（Create）仍拒绝 null。
    config: dict[str, Any] | None = None


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
