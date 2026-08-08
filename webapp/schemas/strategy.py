"""Strategy run request/response Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StrategyParamSchema(BaseModel):
    """Schema describing a single strategy parameter."""

    name: str
    type: str  # int / float / str / bool / multi_factor / json / category_weights / category_exponents
    default: Any = None
    label: str = ""
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None
    options: list[Any] = Field(default_factory=list)


class StrategyMeta(BaseModel):
    """Metadata for a strategy type."""

    name: str
    display_name: str
    description: str
    params_schema: list[StrategyParamSchema] = Field(default_factory=list)


class StrategyRunRequest(BaseModel):
    """Request to run a strategy."""

    strategy_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None


class StrategyMetrics(BaseModel):
    """Performance metrics for a strategy run."""

    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe: float
    max_drawdown: float
    win_rate: float | None = None


class ConstraintViolationItem(BaseModel):
    """A single constraint validation result."""

    constraint: str
    message: str
    severity: str = "error"


class StrategyRunSummary(BaseModel):
    """Summary returned immediately after a strategy run."""

    run_id: int
    strategy_type: str
    status: str
    metrics: StrategyMetrics | None = None
    nav_series: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    constraint_violations: list[ConstraintViolationItem] = Field(default_factory=list)
    error_msg: str | None = None


class StrategyRunListItem(BaseModel):
    """List item for a strategy run record."""

    model_config = {"from_attributes": True}

    id: int
    strategy_type: str
    params: dict[str, Any]
    status: str
    start_date: str | None = None
    end_date: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_msg: str | None = None


class StrategyRunDetail(BaseModel):
    """Full detail of a strategy run."""

    model_config = {"from_attributes": True}

    id: int
    strategy_type: str
    params: dict[str, Any]
    universe_snapshot: list[str]
    start_date: str | None = None
    end_date: str | None = None
    result_summary: dict[str, Any]
    status: str
    error_msg: str | None = None
    created_at: datetime
    completed_at: datetime | None = None