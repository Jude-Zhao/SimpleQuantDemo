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
    map_to_availability_dates,
)
from core.analysis.exceptions import AnalysisError
from core.factors import MACDHistFactor


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

    factor = MACDHistFactor().build(price_data, macro_data, universe)
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


def test_map_to_availability_dates_shifts_by_calendar_position() -> None:
    trading = pd.bdate_range("2024-01-01", periods=10)
    labels = pd.Series([1.0, 2.0, 3.0, 4.0], index=trading[[0, 2, 5, 8]], name="icir")

    mapped = map_to_availability_dates(labels, horizon=2, trading_dates=trading)

    # horizon+1 = 3 个交易日后收益实现：0→3、2→5、5→8；尾部不足的标签丢弃
    assert list(mapped.index) == [trading[3], trading[5], trading[8]]
    assert np.allclose(mapped.to_numpy(), [1.0, 2.0, 3.0])


def test_map_to_availability_dates_sparse_rebalance_labels() -> None:
    trading = pd.bdate_range("2024-01-01", periods=20)
    rebalance = trading[[0, 5, 10, 15]]
    labels = pd.Series([1.0, 2.0, 3.0, 4.0], index=rebalance)

    mapped = map_to_availability_dates(labels, horizon=5, trading_dates=trading)

    # 稀疏标签按完整日历位置右移 horizon+1 = 6 个交易日，绝非跳过 6 个稀疏样本
    assert list(mapped.index) == [trading[6], trading[11], trading[16]]
    assert np.allclose(mapped.to_numpy(), [1.0, 2.0, 3.0])


def test_map_to_availability_dates_rejects_invalid_input() -> None:
    trading = pd.bdate_range("2024-01-01", periods=5)
    labels = pd.Series([1.0, 2.0], index=trading[:2])

    with pytest.raises(ValueError, match="horizon"):
        map_to_availability_dates(labels, horizon=0, trading_dates=trading)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        map_to_availability_dates(
            pd.Series([1.0, 2.0], index=[0, 1]), horizon=5, trading_dates=trading
        )

