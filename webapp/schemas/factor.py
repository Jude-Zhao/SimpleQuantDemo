"""Factor-related Pydantic schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FactorParamSchema(BaseModel):
    type: str
    default: Any
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None
    label: str


class FactorMeta(BaseModel):
    id: str
    name: str
    display_name: str
    category: str
    description: str
    formula: str
    direction: str
    params: dict[str, Any] = Field(default_factory=dict)
    params_schema: dict[str, FactorParamSchema]


class FactorCategoryMeta(BaseModel):
    key: str
    display_name: str
    is_empty: bool
    factors: list[FactorMeta] = Field(default_factory=list)


class FactorComputeRequest(BaseModel):
    factor_id: str | None = None
    factor_name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    horizon: int = 5


class FactorICResult(BaseModel):
    ic_mean: float
    ic_std: float
    icir: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_icir: float
    ic_series: dict[str, float]  # date string -> ic value
    rank_ic_series: dict[str, float]
    icir_series: dict[str, float]


class FactorGroupReturn(BaseModel):
    group: int
    annual_return: float
    cumulative_return: float


class FactorComputeResponse(BaseModel):
    factor_name: str
    display_name: str
    ic_result: FactorICResult
    group_returns: list[FactorGroupReturn]
