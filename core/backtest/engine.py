"""Reusable backtest engine shared by research and webapp layers."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.calendar import generate_rebalance_dates, get_trading_dates
from core.factors.utils import pivot_price_field
from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest configuration."""

    rebalance_freq: str = "weekly"
    rebalance_day: int = 0
    top_n: int = 5
    max_weight: float = 0.5
    min_weight: float = 0.0
    transaction_cost_bps: float = 1.0
    weight_mode: str = "equal"  # "equal" (Top-N equal weight) | "score" (score-proportional)


@dataclass(frozen=True)
class BacktestResult:
    """Backtest outputs."""

    equity_curve: pd.Series
    daily_returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    costs: pd.Series
    rebalance_dates: pd.DatetimeIndex


def run_backtest(
    price_data: pd.DataFrame,
    factor_scores: pd.DataFrame,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a simple close-to-close rotation backtest.

    Weights decided on a rebalance date are applied to the following daily
    close-to-close return row. Transaction costs are deducted on rebalance dates
    from that day's portfolio return.
    """
    cfg = config or BacktestConfig()
    close = pivot_price_field(
        price_data, field="close", universe=list(factor_scores.columns)
    )
    close = close.loc[
        close.index.intersection(factor_scores.index), factor_scores.columns
    ].sort_index()
    scores = factor_scores.loc[close.index, close.columns].sort_index()
    daily_asset_returns = close.pct_change(fill_method=None).fillna(0.0)

    trading_dates = get_trading_dates(price_data)
    rebalance_dates = generate_rebalance_dates(
        trading_dates=trading_dates.intersection(close.index),
        rebalance_freq=cfg.rebalance_freq,  # type: ignore[arg-type]
        rebalance_day=cfg.rebalance_day,
    )

    if cfg.weight_mode == "score":
        optimizer: EqualWeightOptimizer | ScoreWeightedOptimizer = ScoreWeightedOptimizer(
            top_n=cfg.top_n,
            max_weight=cfg.max_weight,
            min_weight=cfg.min_weight,
        )
    else:
        optimizer = EqualWeightOptimizer(
            top_n=cfg.top_n,
            max_weight=cfg.max_weight,
            min_weight=cfg.min_weight,
        )
    target_weights = pd.DataFrame(
        pd.NA, index=close.index, columns=close.columns, dtype="Float64"
    )
    turnover = pd.Series(0.0, index=close.index, name="turnover")
    costs = pd.Series(0.0, index=close.index, name="cost")

    previous_weights = pd.Series(0.0, index=close.columns, name="weight")
    for date in rebalance_dates:
        if date not in scores.index:
            continue
        score_row = scores.loc[date]
        if score_row.dropna().empty:
            continue
        new_weights = optimizer.optimize(score_row)
        target_weights.loc[date] = new_weights
        turnover.loc[date] = (new_weights - previous_weights).abs().sum()
        costs.loc[date] = turnover.loc[date] * cfg.transaction_cost_bps / 10000
        previous_weights = new_weights

    weights = target_weights.ffill().fillna(0.0).astype(float)
    # T+1 execution: weights decided on day T are held from the T+1 close, so
    # they only earn returns from T+2 onward. shift(2) avoids using the
    # decision-day close as the execution price (which is a lookahead bias).
    shifted_weights = weights.shift(2).fillna(0.0)
    gross_returns = (shifted_weights * daily_asset_returns).sum(axis=1)
    daily_returns = (gross_returns - costs).rename("daily_return")
    equity_curve = (1.0 + daily_returns).cumprod().rename("equity")

    return BacktestResult(
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        weights=weights,
        turnover=turnover,
        costs=costs,
        rebalance_dates=rebalance_dates,
    )