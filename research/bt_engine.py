"""bt-based backtest engine for the research pipeline.

Weight-driven integration: the research pipeline computes a target-weight
matrix (date × sec) on rebalance dates using the core optimizers, and bt
(bt.portfolio) handles the actual position landing, NAV curve and turnover.
"""

from __future__ import annotations

from dataclasses import dataclass

import bt
import pandas as pd


@dataclass(frozen=True)
class BTBacktestResult:
    """Backtest outputs from the bt engine."""

    equity_curve: pd.Series
    daily_returns: pd.Series
    weights: pd.DataFrame
    bt_result: bt.backtest.Result | None = None


def run_bt_backtest(
    close: pd.DataFrame,
    target_weights: pd.DataFrame,
    rebalance_freq: str = "monthly",
    name: str = "research",
) -> BTBacktestResult:
    """Run a bt backtest that rebalances to ``target_weights`` periodically.

    Args:
        close: date × sec close price matrix (aligned with ``target_weights``).
        target_weights: date × sec target weight matrix (holds weights between
            rebalance dates; bt only acts on trigger bars).
        rebalance_freq: "weekly", "monthly", or "5d" (every 5 trading days).
        name: strategy name used for the bt result series.
    """
    if rebalance_freq not in {"weekly", "monthly", "5d"}:
        raise ValueError(
            f"rebalance_freq must be 'weekly', 'monthly', or '5d', got {rebalance_freq!r}"
        )

    if rebalance_freq == "weekly":
        trigger = bt.algos.RunWeekly()
    elif rebalance_freq == "monthly":
        trigger = bt.algos.RunMonthly()
    else:
        trigger = bt.algos.RunEveryNPeriods(5)
    strategy = bt.Strategy(
        name,
        algos=[
            trigger,
            bt.algos.SelectAll(),
            bt.algos.WeighTarget(target_weights.astype(float)),
            bt.algos.Rebalance(),
        ],
    )
    result = bt.run(bt.Backtest(strategy, close))

    prices = result.prices[name].astype(float)
    # bt prepends an initial 100.0 bar before the data range; align to close.
    prices = prices.loc[prices.index.intersection(close.index)]
    equity_curve = prices.rename("equity").sort_index()
    daily_returns = equity_curve.pct_change().fillna(0.0).rename("daily_return")

    return BTBacktestResult(
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        weights=target_weights.sort_index(),
        bt_result=result,
    )