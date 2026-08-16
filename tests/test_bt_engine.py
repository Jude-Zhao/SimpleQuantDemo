"""bt backtest engine tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.bt_engine import run_bt_backtest


def _close() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2024-01-01", "2024-06-30")
    secs = ["A", "B", "C", "D"]
    return pd.DataFrame(
        np.cumprod(1 + rng.normal(0, 0.01, (len(dates), len(secs))), axis=0),
        index=dates,
        columns=secs,
    )


def _changing_weights(close: pd.DataFrame) -> pd.DataFrame:
    """Target weights that rotate which two assets are held each period."""
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for i, date in enumerate(close.index):
        period = (i // 5) % 2
        top = close.columns[period * 2 : period * 2 + 2]
        weights.loc[date, top] = 0.5
    return weights


def test_run_bt_backtest_returns_expected() -> None:
    close = _close()
    weights = _changing_weights(close)
    result = run_bt_backtest(close, weights, rebalance_freq="monthly")

    assert result.equity_curve.index.equals(close.index)
    assert len(result.daily_returns) == len(close)
    assert result.weights.shape == close.shape
    assert result.equity_curve.iloc[-1] > 0
    assert result.bt_result is not None


def test_rebalance_freq_invalid() -> None:
    close = _close()
    weights = _changing_weights(close)
    with pytest.raises(ValueError):
        run_bt_backtest(close, weights, rebalance_freq="daily")


def test_weekly_and_monthly_produce_different_nav() -> None:
    """Different rebalance triggers must yield different NAV series."""
    close = _close()
    weights = _changing_weights(close)
    weekly = run_bt_backtest(close, weights, rebalance_freq="weekly")
    monthly = run_bt_backtest(close, weights, rebalance_freq="monthly")
    assert not weekly.equity_curve.equals(monthly.equity_curve)


def test_5d_produces_different_nav_from_monthly() -> None:
    close = _close()
    weights = _changing_weights(close)
    five = run_bt_backtest(close, weights, rebalance_freq="5d")
    monthly = run_bt_backtest(close, weights, rebalance_freq="monthly")
    assert not five.equity_curve.equals(monthly.equity_curve)


def test_no_lookahead_on_rebalance_day() -> None:
    """bt engine must execute at decision-day+1 close, not decision-day close.

    A jumps to 1.1 on the rebalance date (index 5) and pulls back to 1.0 the
    next day (index 6). If bt executed at the decision-day close, the -9%
    pullback would be captured. T+1 execution must give 0 on index 6.
    """
    dates = pd.bdate_range("2024-01-01", periods=10)
    secs = ["A", "B"]
    close = pd.DataFrame(
        data={
            "A": [1.0] * 5 + [1.1] + [1.0] * 4,
            "B": [1.0] * 10,
        },
        index=dates,
        columns=secs,
    )
    # Decision dates: index 0 -> B, index 5 -> A. NaN elsewhere; engine ffill's.
    tw = pd.DataFrame(float("nan"), index=dates, columns=secs)
    tw.loc[dates[0], "B"] = 1.0
    tw.loc[dates[5], "A"] = 1.0

    res = run_bt_backtest(close, tw, rebalance_freq="5d")

    # index 6: A pulls back 1.1 -> 1.0. T+1 execution -> 0.
    assert res.daily_returns.loc[dates[6]] == pytest.approx(0.0)