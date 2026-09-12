"""统一回测框架的共享数据模型。

ExecutionConfig / BacktestConfig / MetricsConfig / BacktestResult / TargetPlan
统一定义在本模块，engine / targets / metrics 只导入 models，避免循环导入；
`core.backtest.__init__` 统一对外导出。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class ExecutionConfig:
    """执行账本配置（费用与初始资金）。

    transaction_cost_bps 单位为基点：0.5 = 万分之0.5 = 0.00005。
    """

    initial_cash: float = 1.0
    transaction_cost_bps: float = 0.5


@dataclass(frozen=True)
class BacktestConfig:
    """回测选股/调度配置（run_backtest 与 build_target_weights 使用）。

    注意：字段名沿用现有 rebalance_freq / weight_mode，不得改名。
    """

    rebalance_freq: str = "weekly"
    rebalance_day: int = 0
    top_n: int = 5
    max_weight: float = 0.5
    min_weight: float = 0.0
    transaction_cost_bps: float = 0.5
    weight_mode: str = "equal"  # "equal" (Top-N 等权) | "score" (得分占比)


@dataclass(frozen=True)
class MetricsConfig:
    """绩效指标配置。默认年无风险利率 1%（仅用于风险调整，不给现金计息）。"""

    annualization: int = 252
    risk_free_rate: float = 0.01


@dataclass(frozen=True)
class TargetPlan:
    """build_target_weights 的输出：稀疏目标矩阵 + 全部计划决策日 + 决策日志。"""

    target_weights: pd.DataFrame
    rebalance_dates: pd.DatetimeIndex
    decision_log: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class BacktestResult:
    """回测账本输出。

    语义约定（与旧版差异见 docs/统一回测与绩效框架.md）：
    - equity_curve：归一化净值（资产金额 / initial_cash），首行为装饰性初始值 1.0；
    - daily_returns：首行为基准 0，不计收益期间；费用已包含；
    - weights：每日收盘后的实际持仓权重（不再是目标权重）；
    - costs：费用 / 当日成交前净值（比率），不能把 sum(costs) 当作现金金额；
    - turnover：实际成交总金额 / 当日成交前净值（买卖合计，不除 2）；
    - fees：当日费用绝对金额；
    - target_weights：稀疏目标矩阵（仅决策日有行）；
    - trades / execution_log / decision_log：逐笔成交、执行与决策记录。
    """

    equity_curve: pd.Series
    daily_returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    costs: pd.Series
    rebalance_dates: pd.DatetimeIndex
    target_weights: pd.DataFrame | None = None
    cash: pd.Series | None = None
    holdings: pd.DataFrame | None = None
    fees: pd.Series | None = None
    trades: pd.DataFrame | None = None
    execution_log: list[dict] = field(default_factory=list)
    decision_log: list[dict] = field(default_factory=list)
    initial_cash: float = 1.0
    config_snapshot: dict = field(default_factory=dict)
