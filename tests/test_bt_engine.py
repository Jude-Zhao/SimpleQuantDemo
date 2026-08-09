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