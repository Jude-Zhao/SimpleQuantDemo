from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.factors import (
    Drawdown120Factor,
    MACDHistFactor,
    MFIFactor,
    PSY20Factor,
    Skewness60ReversalFactor,
)
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


def _long_price_data(n: int = 70) -> pd.DataFrame:
    """Monotonically increasing close for windowed-factor formula tests."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = [10.0 + i for i in range(n)]
    opens = [10.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "date": list(dates) * 2,
            "sec": ["510300.SH"] * n + ["159928.SZ"] * n,
            "open": opens * 2,
            "high": [c + 0.5 for c in closes] * 2,
            "low": [c - 0.5 for c in closes] * 2,
            "close": closes * 2,
            "volume": [100] * (n * 2),
            "amount": [1000] * (n * 2),
        }
    )


def _empty_macro() -> pd.DataFrame:
    return pd.DataFrame(index=pd.date_range("2026-01-01", periods=70, freq="D"))


def _universe() -> list[str]:
    return ["510300.SH", "159928.SZ"]


def test_pivot_price_field_keeps_universe_order() -> None:
    price_data = _sample_price_data()
    matrix = pivot_price_field(price_data, universe=["159928.SZ", "510300.SH"])

    assert matrix.columns.tolist() == ["159928.SZ", "510300.SH"]
    assert matrix.index[0] == pd.Timestamp("2026-01-01")
    assert matrix.loc[pd.Timestamp("2026-01-03"), "510300.SH"] == 12


def test_psy20_formula() -> None:
    factor = PSY20Factor().build(
        _long_price_data(n=80), _empty_macro(), _universe()
    )
    close = pivot_price_field(_long_price_data(n=80), universe=_universe())
    ret = close.pct_change(fill_method=None)
    expected = (ret > 0.0).where(ret.notna()).rolling(20).mean()

    pd.testing.assert_frame_equal(factor, expected)


def test_drawdown_120_formula() -> None:
    factor = Drawdown120Factor().build(
        _long_price_data(n=80), _empty_macro(), _universe()
    )
    close = pivot_price_field(_long_price_data(n=80), universe=_universe())
    dd = close / close.cummax() - 1.0
    expected = dd.rolling(120, min_periods=1).min()

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
    close = pivot_price_field(price_data, universe=universe)

    factors = {
        MACDHistFactor().name: MACDHistFactor().build(price_data, macro_data, universe),
        Skewness60ReversalFactor().name: Skewness60ReversalFactor().build(
            price_data, macro_data, universe
        ),
        MFIFactor().name: MFIFactor().build(price_data, macro_data, universe),
        PSY20Factor().name: PSY20Factor().build(price_data, macro_data, universe),
        Drawdown120Factor().name: Drawdown120Factor().build(
            price_data, macro_data, universe
        ),
    }

    validate_factor_panel(factors, universe)
    for name, matrix in factors.items():
        assert matrix.shape == close.shape
        assert int(matrix.notna().sum().sum()) > 0