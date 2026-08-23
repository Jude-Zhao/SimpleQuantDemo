"""Tests for the evaluate_factor convenience entrypoint (core.evaluate)."""

from __future__ import annotations

import pandas as pd
import pytest

from core.evaluate import evaluate_factor
from core.factors.registry import get_factor_class

_METRIC_KEYS = (
    "total_return",
    "annual_return",
    "annual_volatility",
    "sharpe",
    "max_drawdown",
)


def _macd():
    cls = get_factor_class("macd_hist")
    assert cls is not None
    return cls()


def _load(sqlite_source):
    return sqlite_source.load_all(start_date="2024-01-01", end_date="2026-03-13")


def test_evaluate_without_backtest(sqlite_source) -> None:
    price_data, macro, universe = _load(sqlite_source)
    res = evaluate_factor(
        _macd(),
        price_data=price_data,
        macro_data=macro,
        universe=universe,
    )
    assert isinstance(res.ic_mean, float)
    assert res.n_observations > 0
    assert res.factor.shape[1] == len(universe)
    assert res.factor.index.name == "date"
    assert "macd_hist" in res.name
    assert res.backtest is None  # 未开启回测


def test_evaluate_with_data_source_and_backtest(sqlite_source) -> None:
    res = evaluate_factor(
        _macd(),
        data_source=sqlite_source,
        start_date="2024-01-01",
        end_date="2026-03-13",
        backtest=True,
        backtest_freq="5d",
        backtest_top_n=3,
    )
    assert res.backtest is not None
    assert all(k in res.backtest.metrics for k in _METRIC_KEYS)
    assert res.backtest.n_rebalances > 0
    assert not res.backtest.equity_curve.empty


def test_evaluate_infers_universe_from_price(sqlite_source) -> None:
    price_data, macro, universe = _load(sqlite_source)
    # 不显式传 universe → 从 price_data 自动推断
    res = evaluate_factor(_macd(), price_data=price_data, macro_data=macro)
    assert res.factor.shape[1] == len(universe)
    assert set(res.factor.columns) == set(universe)


def test_evaluate_score_mode_backtest(sqlite_source) -> None:
    res = evaluate_factor(
        _macd(),
        data_source=sqlite_source,
        start_date="2024-01-01",
        end_date="2026-03-13",
        backtest=True,
        backtest_weight_mode="score",
    )
    assert res.backtest is not None
    assert res.backtest.n_rebalances > 0


def test_infer_universe_requires_sec_column() -> None:
    df = pd.DataFrame({"date": ["2024-01-01"], "close": [1.0]})
    with pytest.raises(ValueError):
        evaluate_factor(_macd(), price_data=df, universe=None)