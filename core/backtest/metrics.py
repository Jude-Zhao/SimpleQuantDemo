"""唯一绩效指标计算（B4 / BUG-11）。

约定（与 docs/统一回测与绩效框架.md 一致）：
- 输入为 BacktestResult：equity_curve 为归一化净值、首行是装饰性初始值 1.0；
  daily_returns 首行是基准 0，不计收益期间；后续持现金的零收益日仍是有效
  期间，不能 drop 所有 0。
- 指标不四舍五入；展示层才舍入。
- 默认年无风险利率 1%（0.01），只用于风险调整，不给现金自动计息。
- 有效收益期间数 n = len(daily_returns) - 1（去掉首行基准）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.backtest.models import BacktestResult, MetricsConfig

_ZERO_METRICS: dict[str, float] = {
    "total_return": 0.0,
    "annual_return": 0.0,
    "annual_volatility": 0.0,
    "sharpe": 0.0,
    "max_drawdown": 0.0,
    "sortino": 0.0,
    "calmar": 0.0,
    "win_rate": 0.0,
    "turnover_sum": 0.0,
    "cost_sum": 0.0,
    "fee_amount_sum": 0.0,
    "rebalance_count": 0.0,
}


def _max_drawdown(equity: pd.Series) -> float:
    """max_drawdown = min(equity / cummax(equity) - 1)。

    净值序列必须含装饰性首行 1.0（峰值序列因此自动包含初始本金 1）；若传入
    序列不含初始行（首值 != 1），先在头部拼接 1.0 再取 cummax——例如
    [0.999, 0.999, 0.999] 须报 -0.001 而非 0。
    """
    values = equity.astype(float)
    if float(values.iloc[0]) != 1.0:
        values = pd.concat([pd.Series([1.0]), values], ignore_index=True)
    peak = values.cummax()
    return float((values / peak - 1.0).min())


def calculate_metrics(
    result: BacktestResult,
    config: MetricsConfig | None = None,
) -> dict[str, float]:
    """从 BacktestResult 计算全部约定指标（唯一实现）。

    Raises:
        ValueError: 收益含非有限值、净值非正、净值/日收益长度不一致、
            annualization <= 0 或 risk_free_rate <= -1。
    """
    mc = config or MetricsConfig()
    if mc.annualization <= 0:
        raise ValueError(f"annualization 必须为正，得到 {mc.annualization!r}")
    if mc.risk_free_rate <= -1:
        raise ValueError(f"risk_free_rate 必须 > -1，得到 {mc.risk_free_rate!r}")

    metrics = dict(_ZERO_METRICS)
    equity = result.equity_curve
    daily = result.daily_returns
    if equity is None or daily is None or (len(equity) == 0 and len(daily) == 0):
        return metrics

    equity = equity.astype(float)
    daily = daily.astype(float)
    if len(equity) != len(daily):
        raise ValueError(
            f"净值与日收益长度不一致: {len(equity)} vs {len(daily)}"
        )
    if not np.isfinite(daily.to_numpy()).all():
        raise ValueError("日收益含 NaN/inf，非法输入")
    if not np.isfinite(equity.to_numpy()).all() or (equity <= 0).any():
        raise ValueError("净值必须为正有限值")

    ann = int(mc.annualization)
    rf_daily = (1.0 + float(mc.risk_free_rate)) ** (1.0 / ann) - 1.0

    metrics["total_return"] = float(equity.iloc[-1] - 1.0)
    metrics["max_drawdown"] = _max_drawdown(equity)

    # 有效收益期间：去掉首行基准 0。
    r = daily.iloc[1:]
    n = len(r)
    if n == 0:
        return metrics

    excess = r.to_numpy() - rf_daily

    if metrics["total_return"] > -1.0:
        metrics["annual_return"] = float(
            (1.0 + metrics["total_return"]) ** (ann / n) - 1.0
        )
    else:
        metrics["annual_return"] = -1.0

    if n >= 2:
        metrics["annual_volatility"] = float(np.std(r.to_numpy(), ddof=1) * np.sqrt(ann))
        std_excess = float(np.std(excess, ddof=1))
        if std_excess > 0:
            metrics["sharpe"] = float(np.mean(excess) / std_excess * np.sqrt(ann))

    downside = np.minimum(excess, 0.0)
    downside_rms = float(np.sqrt(np.mean(downside**2)))
    if downside_rms > 0:
        metrics["sortino"] = float(np.mean(excess) * np.sqrt(ann) / downside_rms)

    nonzero = r.to_numpy() != 0.0
    if nonzero.any():
        metrics["win_rate"] = float((r.to_numpy() > 0).sum() / nonzero.sum())

    if metrics["max_drawdown"] != 0.0:
        metrics["calmar"] = float(metrics["annual_return"] / abs(metrics["max_drawdown"]))

    turnover = getattr(result, "turnover", None)
    if turnover is not None and len(turnover) > 0:
        turnover = turnover.astype(float)
        if not np.isfinite(turnover.to_numpy()).all():
            raise ValueError("turnover 含 NaN/inf，非法输入")
        metrics["turnover_sum"] = float(turnover.sum())

    fees = getattr(result, "fees", None)
    fee_amount_sum = 0.0
    if fees is not None and len(fees) > 0:
        fees = fees.astype(float)
        if not np.isfinite(fees.to_numpy()).all():
            raise ValueError("fees 含 NaN/inf，非法输入")
        fee_amount_sum = float(fees.sum())
    metrics["fee_amount_sum"] = fee_amount_sum
    initial_cash = float(getattr(result, "initial_cash", 1.0) or 1.0)
    metrics["cost_sum"] = fee_amount_sum / initial_cash

    execution_log = getattr(result, "execution_log", None) or []
    metrics["rebalance_count"] = float(
        sum(1 for entry in execution_log if entry.get("status") == "executed")
    )
    return metrics


def calculate_yearly_returns(daily_returns: pd.Series) -> dict[int, float]:
    """按日收益年份分组计算年度收益 (1+r) 连乘 - 1。

    首行基准 0 不改变年度值。空输入返回 {}。
    """
    if daily_returns is None or len(daily_returns) == 0:
        return {}
    if not isinstance(daily_returns.index, pd.DatetimeIndex):
        raise ValueError("日收益索引必须是 DatetimeIndex 才能按年聚合")
    r = daily_returns.astype(float)
    if not np.isfinite(r.to_numpy()).all():
        raise ValueError("日收益含 NaN/inf，非法输入")
    yearly: dict[int, float] = {}
    for year, grp in r.groupby(r.index.year):
        yearly[int(year)] = float((1.0 + grp).prod() - 1.0)
    return yearly
