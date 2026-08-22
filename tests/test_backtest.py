from __future__ import annotations

import pandas as pd
import pytest

from core.analysis import calculate_factor_ic, calculate_forward_returns, calculate_icir
from core.factors import MACDHistFactor, Drawdown120Factor
from core.synthesis import ICIRWeightedSynthesizer
from research.backtest import BacktestConfig, run_backtest


def test_run_backtest_minimal_deterministic_case() -> None:
    dates = pd.date_range("2026-01-05", periods=6, freq="D")
    price_data = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["A.SH"] * 6 + ["B.SH"] * 6,
            "open": [1, 2, 3, 4, 5, 6, 1, 1, 1, 1, 1, 1],
            "high": [1, 2, 3, 4, 5, 6, 1, 1, 1, 1, 1, 1],
            "low": [1, 2, 3, 4, 5, 6, 1, 1, 1, 1, 1, 1],
            "close": [1, 2, 3, 4, 5, 6, 1, 1, 1, 1, 1, 1],
            "volume": [100] * 12,
            "amount": [100] * 12,
        }
    )
    factor_scores = pd.DataFrame(
        [[1.0, 0.0]] * 6,
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH"],
    )

    result = run_backtest(
        price_data,
        factor_scores,
        BacktestConfig(top_n=1, max_weight=1.0, transaction_cost_bps=0),
    )

    assert result.weights.loc[pd.Timestamp("2026-01-05"), "A.SH"] == pytest.approx(1.0)
    # T+1 execution: decision 01-05, executed at 01-06 close, held from 01-07.
    assert result.daily_returns.loc[pd.Timestamp("2026-01-06")] == pytest.approx(0.0)
    assert result.equity_curve.iloc[-1] == pytest.approx(3.0)


def test_run_backtest_charges_turnover_cost() -> None:
    dates = pd.date_range("2026-01-05", periods=6, freq="D")
    price_data = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["A.SH"] * 6 + ["B.SH"] * 6,
            "open": [1] * 12,
            "high": [1] * 12,
            "low": [1] * 12,
            "close": [1] * 12,
            "volume": [100] * 12,
            "amount": [100] * 12,
        }
    )
    factor_scores = pd.DataFrame(
        [[1.0, 0.0]] * 6,
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH"],
    )

    result = run_backtest(
        price_data,
        factor_scores,
        BacktestConfig(top_n=1, max_weight=1.0, transaction_cost_bps=10),
    )

    assert result.turnover.loc[pd.Timestamp("2026-01-05")] == pytest.approx(1.0)
    assert result.costs.loc[pd.Timestamp("2026-01-05")] == pytest.approx(0.001)
    assert result.equity_curve.iloc[0] == pytest.approx(0.999)


def test_run_backtest_clears_sold_positions_on_rebalance() -> None:
    dates = pd.to_datetime(
        [
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
            "2026-01-08",
            "2026-01-09",
            "2026-01-12",
            "2026-01-13",
        ]
    )
    price_data = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["A.SH"] * len(dates) + ["B.SH"] * len(dates),
            "open": [1] * (len(dates) * 2),
            "high": [1] * (len(dates) * 2),
            "low": [1] * (len(dates) * 2),
            "close": [1] * (len(dates) * 2),
            "volume": [100] * (len(dates) * 2),
            "amount": [100] * (len(dates) * 2),
        }
    )
    factor_scores = pd.DataFrame(
        [[1.0, 0.0]] * 5 + [[0.0, 1.0]] * 2,
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH"],
    )

    result = run_backtest(
        price_data,
        factor_scores,
        BacktestConfig(top_n=1, max_weight=1.0, transaction_cost_bps=0),
    )

    assert result.weights.loc[pd.Timestamp("2026-01-09"), "A.SH"] == pytest.approx(1.0)
    assert result.weights.loc[pd.Timestamp("2026-01-09"), "B.SH"] == pytest.approx(0.0)
    assert result.weights.loc[pd.Timestamp("2026-01-12"), "A.SH"] == pytest.approx(0.0)
    assert result.weights.loc[pd.Timestamp("2026-01-12"), "B.SH"] == pytest.approx(1.0)


def test_run_backtest_with_example_pipeline(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )
    factor_panel = {
        "momentum_5": MACDHistFactor().build(price_data, macro_data, universe),
        "volatility_20": Drawdown120Factor().build(price_data, macro_data, universe),
    }
    forward_returns = calculate_forward_returns(price_data, horizon=5, universe=universe)
    icir_data = {
        factor_name: calculate_icir(
            calculate_factor_ic(factor, forward_returns, min_observations=10),
            window=20,
            min_periods=10,
        )
        for factor_name, factor in factor_panel.items()
    }
    synthesized = ICIRWeightedSynthesizer().synthesize(factor_panel, icir_data)

    result = run_backtest(
        price_data,
        synthesized,
        BacktestConfig(top_n=5, max_weight=0.5, transaction_cost_bps=5),
    )

    assert result.equity_curve.index.equals(synthesized.index)
    assert result.weights.shape == synthesized.shape
    assert int((result.weights.sum(axis=1) > 0).sum()) > 0
    assert result.equity_curve.dropna().iloc[-1] > 0


def test_run_backtest_no_lookahead_on_rebalance_day() -> None:
    """V-shape: asset jumps on a rebalance date, pulls back the next day.

    T+1 execution must NOT capture the rebalance-day-to-next-day price gap.
    Decision on day5 picks A (A jumps to 1.1 that day, pulls back to 1.0 day6).
    A T-day-close execution would capture the -9% pullback; T+1 must give 0.
    """
    dates = pd.date_range("2026-01-05", periods=10, freq="D")
    a_close = [1.0] * 5 + [1.1] + [1.0] * 4
    price_data = pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["A.SH"] * 10 + ["B.SH"] * 10,
            "open": a_close + [1.0] * 10,
            "high": a_close + [1.0] * 10,
            "low": a_close + [1.0] * 10,
            "close": a_close + [1.0] * 10,
            "volume": [100] * 20,
            "amount": [100] * 20,
        }
    )
    # Factor prefers B for days 0-4, switches to A from day5 (so day5 picks A).
    factor_scores = pd.DataFrame(
        [[0.0, 1.0]] * 5 + [[1.0, 0.0]] * 5,
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH"],
    )

    result = run_backtest(
        price_data,
        factor_scores,
        BacktestConfig(top_n=1, max_weight=1.0, transaction_cost_bps=0, rebalance_freq="5d"),
    )

    # day6: A pulls back 1.1 -> 1.0. With T+1 execution this is not captured.
    assert result.daily_returns.loc[dates[6]] == pytest.approx(0.0)
