"""Strategy run request/response Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, Field, field_validator


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


_REBALANCE_FREQS = ("weekly", "monthly", "5d")


def _require_finite_number(value: Any, name: str) -> float:
    """F11: 参数数值校验——拒绝 bool/字符串/None/NaN/Inf。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须为数值，收到 {value!r}")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} 必须为有限数，收到 {value!r}")
    return number


class StrategyRunRequest(BaseModel):
    """Request to run a strategy."""

    strategy_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None

    @field_validator("params")
    @classmethod
    def _validate_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        """F11: 提交前校验已知参数键（服务端契约，前端滑杆限制不构成约束）：
        beta 为正有限数、top_n 为正整数、调仓频率合法、权重/指数非负有限且
        非空时有正项。非法值 422 拒绝，不带病进入任务；核心合成函数保留
        同等校验兜底。未识别的键不在此约束（params 保持自由字典）。
        """
        if "top_n" in params:
            top_n = params["top_n"]
            integral = (isinstance(top_n, int) and not isinstance(top_n, bool)) or (
                isinstance(top_n, float) and isfinite(top_n) and top_n.is_integer()
            )
            if not integral or int(top_n) < 1:
                raise ValueError(f"top_n 必须为正整数，收到 {top_n!r}")
        if "rebalance_freq" in params:
            freq = params["rebalance_freq"]
            if not isinstance(freq, str) or freq not in _REBALANCE_FREQS:
                raise ValueError(
                    f"rebalance_freq 必须为 {'/'.join(_REBALANCE_FREQS)} 之一，"
                    f"收到 {freq!r}"
                )
        if params.get("beta") is not None:
            beta = _require_finite_number(params["beta"], "beta")
            if beta <= 0:
                raise ValueError(
                    f"beta 必须为正数，收到 {params['beta']!r}：β=0 会把缺失格"
                    "激活为合格（NaN**0=1），负 β 会颠倒排序"
                )
        for key in ("class_weights", "exponents"):
            mapping = params.get(key)
            if mapping is None:
                continue
            if not isinstance(mapping, dict):
                raise ValueError(f"{key} 必须为 {{类别: 数值}} 字典，收到 {mapping!r}")
            has_positive = False
            for cat, weight in mapping.items():
                checked = _require_finite_number(weight, f"{key}.{cat}")
                if checked < 0:
                    raise ValueError(f"{key}.{cat} 必须为非负数，收到 {weight!r}")
                has_positive = has_positive or checked > 0
            if mapping and not has_positive:
                raise ValueError(f"{key} 至少需要一个正项")
        return params


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
