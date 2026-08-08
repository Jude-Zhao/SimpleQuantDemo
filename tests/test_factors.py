from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.factors import MomentumFactor, VolatilityFactor
from core.factors.exceptions import FactorValidationError
from core.factors.utils import pivot_price_field, validate_factor_panel


def _sample_price_data() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    return pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["510300.SH"] * 6 + ["159928.SZ"] * 6,
            "open": [10, 11, 12, 13, 14, 15, 20, 20, 21, 22, 23, 24],
            "high": [10, 11, 12, 13, 14, 15, 20, 20, 21, 22, 23, 24],
            "low": [10, 11, 12, 13, 14, 15, 20, 20, 21, 22, 23, 24],
            "close": [10, 11, 12, 13, 14, 15, 20, 20, 21, 22, 23, 24],
            "volume": [100] * 12,
            "amount": [1000] * 12,
        }
    )


def _empty_macro() -> pd.DataFrame:
    return pd.DataFrame(index=pd.date_range("2026-01-01", periods=6, freq="D"))


def test_pivot_price_field_keeps_universe_order() -> None:
    price_data = _sample_price_data()
    matrix = pivot_price_field(price_data, universe=["159928.SZ", "510300.SH"])

    assert matrix.columns.tolist() == ["159928.SZ", "510300.SH"]
    assert matrix.index[0] == pd.Timestamp("2026-01-01")
    assert matrix.loc[pd.Timestamp("2026-01-03"), "510300.SH"] == 12


def test_momentum_factor_formula() -> None:
    factor = MomentumFactor(window=2).build(
        _sample_price_data(),
        _empty_macro(),
        ["510300.SH", "159928.SZ"],
    )

    assert factor.columns.tolist() == ["510300.SH", "159928.SZ"]
    assert np.isnan(factor.loc[pd.Timestamp("2026-01-02"), "510300.SH"])
    assert factor.loc[pd.Timestamp("2026-01-03"), "510300.SH"] == pytest.approx(0.2)
    assert factor.loc[pd.Timestamp("2026-01-04"), "159928.SZ"] == pytest.approx(0.1)


def test_volatility_factor_formula() -> None:
    price_data = _sample_price_data()
    factor = VolatilityFactor(window=3, annualization=252).build(
        price_data,
        _empty_macro(),
        ["510300.SH", "159928.SZ"],
    )
    close = pivot_price_field(price_data, universe=["510300.SH", "159928.SZ"])
    expected = close.pct_change(fill_method=None).rolling(3, min_periods=3).std() * np.sqrt(252)

    pd.testing.assert_frame_equal(factor, expected)


def test_validate_factor_panel_rejects_wrong_columns() -> None:
    factor = pd.DataFrame(
        [[1.0]],
        index=pd.DatetimeIndex([pd.Timestamp("2026-01-01")], name="date"),
        columns=["510300.SH"],
    )

    with pytest.raises(FactorValidationError):
        validate_factor_panel({"bad_factor": factor}, ["159928.SZ"])


def test_builtin_factors_build_on_example_data(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )

    momentum = MomentumFactor(window=5).build(price_data, macro_data, universe)
    volatility = VolatilityFactor(window=20).build(price_data, macro_data, universe)
    close = pivot_price_field(price_data, universe=universe)

    validate_factor_panel({MomentumFactor().name: momentum, VolatilityFactor().name: volatility}, universe)
    assert momentum.shape == close.shape
    assert volatility.shape == close.shape
    assert momentum.index.min() == close.index.min()
    assert volatility.index.max() == close.index.max()
    assert int(momentum.notna().sum().sum()) > 0
    assert int(volatility.notna().sum().sum()) > 0
