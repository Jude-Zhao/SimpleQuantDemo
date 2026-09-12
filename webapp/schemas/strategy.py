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
    """Performance metrics for a strategy run.

    统一框架产出全部指标；历史记录可能缺字段，因此新增字段全部可选。
    """

    total_return: float
    annual_return: float
    annual_volatility: float
    sharpe: float
    max_drawdown: float
    win_rate: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    turnover_sum: float | None = None
    cost_sum: float | None = None
    fee_amount_sum: float | None = None
    rebalance_count: float | None = None


class ConstraintViolationItem(BaseModel):
    """A single constraint validation result.

    target/actual/limit/unit 为可选结构化字段（新结果写入）；旧记录仍只有
    constraint/message/severity，以 dict 兼容读取。
    """

    constraint: str
    message: str
    severity: str = "error"
    target: str | None = None
    actual: float | None = None
    limit: float | None = None
    unit: str | None = None  # weight / turnover


class RunContext(BaseModel):
    """运行时上下文快照（B7）：有效参数、名称映射、因子实例、分类与约束。"""

    strategy_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    sec_names: dict[str, str] = Field(default_factory=dict)
    factor_categories: list[dict[str, Any]] = Field(default_factory=list)
    classifications: dict[str, dict[str, str]] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)


class DecisionExclusion(BaseModel):
    """单个不合格标的的原因记录（同名不同参数实例逐条保留）。"""

    sec: str
    category: str | None = None
    factor: str | None = None
    instance_index: int | None = None
    params: dict[str, Any] | None = None
    reason: str


class DecisionLogItem(BaseModel):
    """一个计划调仓决策日的资格与组合生成结果。"""

    decision_date: str
    eligible_count: int
    top_n: int
    status: str  # target_created / skipped_insufficient
    exclusions: list[DecisionExclusion] = Field(default_factory=list)


class ExecutionLogItem(BaseModel):
    """一个决策的执行结果（target_created 不等于 executed）。"""

    decision_date: str
    execution_date: str | None = None
    status: str  # executed / no_trade / cancelled_missing_price / unexecuted_end
    reason: str | None = None
    missing_codes: list[str] = Field(default_factory=list)


class ConstraintCheckItem(BaseModel):
    """一次约束检查记录（history 逐执行日 / latest 推荐）。"""

    scope: str  # history / latest
    decision_date: str | None = None
    execution_date: str | None = None
    status: str  # checked / not_evaluated
    violations: list[ConstraintViolationItem] = Field(default_factory=list)
    unsupported_constraints: list[str] = Field(default_factory=list)
    not_evaluated_constraints: list[str] = Field(default_factory=list)


class RunDiagnostics(BaseModel):
    """新结果（schema_version='2'）的诊断部分，写入前严格校验。"""

    schema_version: str
    framework_version: str
    execution_config: dict[str, Any] = Field(default_factory=dict)
    metrics_config: dict[str, Any] = Field(default_factory=dict)
    run_context: RunContext
    decision_log: list[DecisionLogItem] = Field(default_factory=list)
    execution_log: list[ExecutionLogItem] = Field(default_factory=list)
    constraint_checks: list[ConstraintCheckItem] = Field(default_factory=list)


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
