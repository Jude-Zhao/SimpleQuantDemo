from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.analysis import (
    calculate_factor_ic,
    calculate_factor_panel_ic,
    calculate_forward_returns,
    calculate_icir,
    calculate_rank_ic,
)
from core.analysis.exceptions import AnalysisError
from core.factors import AroonDiffFactor


def _sample_price_data() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    return pd.DataFrame(
        {
            "date": list(dates) * 3,
            "sec": ["A.SH"] * 4 + ["B.SH"] * 4 + ["C.SH"] * 4,
            "open": [10, 11, 12, 13, 20, 19, 18, 17, 30, 30, 30, 30],
            "high": [10, 11, 12, 13, 20, 19, 18, 17, 30, 30, 30, 30],
            "low": [10, 11, 12, 13, 20, 19, 18, 17, 30, 30, 30, 30],
            "close": [10, 11, 12, 13, 20, 19, 18, 17, 30, 30, 30, 30],
            "volume": [100] * 12,
            "amount": [1000] * 12,
        }
    )


def test_calculate_forward_returns() -> None:
    returns = calculate_forward_returns(
        _sample_price_data(),
        horizon=2,
        universe=["A.SH", "B.SH", "C.SH"],
    )

    # Forward return measured from execution day (T+1) to T+1+horizon,
    # aligned with the T+1 backtest convention.
    assert returns.loc[pd.Timestamp("2026-01-01"), "A.SH"] == pytest.approx(13 / 11 - 1)
    assert returns.loc[pd.Timestamp("2026-01-01"), "B.SH"] == pytest.approx(17 / 19 - 1)
    assert returns.loc[pd.Timestamp("2026-01-01"), "C.SH"] == pytest.approx(0.0)
    assert np.isnan(returns.loc[pd.Timestamp("2026-01-03"), "A.SH"])


def test_calculate_factor_ic_and_rank_ic() -> None:
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    factor = pd.DataFrame(
        [[1.0, 2.0, 3.0], [3.0, 2.0, 1.0]],
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH", "C.SH"],
    )
    forward_returns = pd.DataFrame(
        [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]],
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH", "C.SH"],
    )

    ic = calculate_factor_ic(factor, forward_returns, min_observations=3)
    rank_ic = calculate_rank_ic(factor, forward_returns, min_observations=3)

    assert ic.loc[pd.Timestamp("2026-01-01")] == pytest.approx(1.0)
    assert ic.loc[pd.Timestamp("2026-01-02")] == pytest.approx(-1.0)
    assert rank_ic.loc[pd.Timestamp("2026-01-01")] == pytest.approx(1.0)
    assert rank_ic.loc[pd.Timestamp("2026-01-02")] == pytest.approx(-1.0)


def test_calculate_factor_ic_respects_min_observations() -> None:
    factor = pd.DataFrame(
        [[1.0, 2.0, np.nan]],
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-01")], name="date"),
        columns=["A.SH", "B.SH", "C.SH"],
    )
    forward_returns = pd.DataFrame(
        [[0.1, 0.2, 0.3]],
        index=factor.index,
        columns=factor.columns,
    )

    ic = calculate_factor_ic(factor, forward_returns, min_observations=3)

    assert np.isnan(ic.iloc[0])


def test_calculate_factor_panel_ic() -> None:
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    factor = pd.DataFrame(
        [[1.0, 2.0, 3.0], [1.0, 3.0, 2.0]],
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH", "C.SH"],
    )
    forward_returns = factor.copy()

    result = calculate_factor_panel_ic({"factor_a": factor}, forward_returns, min_observations=3)

    assert list(result) == ["factor_a"]
    assert result["factor_a"].dropna().tolist() == pytest.approx([1.0, 1.0])


def test_calculate_factor_panel_ic_rejects_empty_panel() -> None:
    with pytest.raises(AnalysisError):
        calculate_factor_panel_ic({}, pd.DataFrame([[1.0]]))


def test_calculate_icir() -> None:
    ic = pd.Series(
        [0.1, 0.2, 0.3, 0.4],
        index=pd.date_range("2026-01-01", periods=4, freq="D"),
        name="ic",
    )

    icir = calculate_icir(ic, window=3, min_periods=3)
    expected = ic.rolling(window=3, min_periods=3).mean() / ic.rolling(window=3, min_periods=3).std()

    pd.testing.assert_series_equal(icir, expected.rename("icir"))


def test_calculate_icir_masks_zero_std() -> None:
    ic = pd.Series(
        [0.1, 0.1, 0.1],
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
    )

    icir = calculate_icir(ic, window=3, min_periods=3)

    assert np.isnan(icir.iloc[-1])


def test_ic_pipeline_with_example_data(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )

    factor = AroonDiffFactor().build(price_data, macro_data, universe)
    forward_returns = calculate_forward_returns(price_data, horizon=5, universe=universe)
    ic = calculate_factor_ic(factor, forward_returns, min_observations=10)
    rank_ic = calculate_rank_ic(factor, forward_returns, min_observations=10)
    icir = calculate_icir(ic, window=20, min_periods=10)

    assert factor.shape == forward_returns.shape
    assert ic.index.equals(factor.index)
    assert rank_ic.index.equals(factor.index)
    assert int(ic.notna().sum()) > 0
    assert int(rank_ic.notna().sum()) > 0
    assert int(icir.notna().sum()) > 0
    assert np.isnan(ic.iloc[-1])

