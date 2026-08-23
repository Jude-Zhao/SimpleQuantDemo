"""bt-based backtest engine for the research pipeline.

Weight-driven integration: the research pipeline computes a target-weight
matrix (date × sec) on rebalance dates using the core optimizers, and bt
(bt.portfolio) handles the actual position landing, NAV curve and turnover.
"""

from __future__ import annotations

from dataclasses import dataclass

import bt
import pandas as pd

from core.calendar import generate_rebalance_dates


@dataclass(frozen=True)
class BTBacktestResult:
    """Backtest outputs from the bt engine."""

    equity_curve: pd.Series
    daily_returns: pd.Series
    weights: pd.DataFrame
    bt_result: bt.backtest.Result | None = None


def _execution_dates(close_index, rebalance_freq: str) -> list:
    """Rebalance (decision) dates shifted forward one trading day.

    Decision happens on the rebalance date T; execution happens at the next
    trading day T+1's close, so the signal price (T close) is never the
    execution price. Returns execution dates that fall inside ``close_index``.
    """
    decision = generate_rebalance_dates(
        trading_dates=pd.DatetimeIndex(close_index),
        rebalance_freq=rebalance_freq,
        rebalance_day=0,
    )
    index = pd.DatetimeIndex(close_index)
    pos = index.get_indexer(decision)
    execution_positions = [p + 1 for p in pos if 0 <= p + 1 < len(index)]
    return [index[p] for p in execution_positions]


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

    # 跨境 ETF / 停牌等原因会使部分 (date, sec) 价格为 NaN；bt 在持仓期间遇 NaN
    # 会抛 "Position is open ... latest price is NaN"。用最后已知价延续（ffill），
    # 残留的开头空洞用后续首个已知价递补（bfill），保证传入 bt 的 close 无 NaN。
    close = close.ffill().bfill()

    # T+1 execution: decide weights on rebalance date T, execute at T+1 close,
    # so the signal close (T) is never the execution close (T+1) — no lookahead.
    filled = target_weights.ffill().fillna(0.0).astype(float)
    execution_dates = _execution_dates(close.index, rebalance_freq)
    trigger = bt.algos.RunOnDate(*execution_dates)
    strategy = bt.Strategy(
        name,
        algos=[
            trigger,
            bt.algos.SelectAll(),
            bt.algos.WeighTarget(filled),
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