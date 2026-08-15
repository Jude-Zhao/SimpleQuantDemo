"""Tests for the factor registry and auto-discovery mechanism."""

from __future__ import annotations

import pandas as pd

from core.factors.registry import (
    discover_factors,
    get_factor_class,
    list_factor_names,
)


def _long_price_data(n: int = 70) -> pd.DataFrame:
    """Monotonically increasing close, two securities, with volume."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    closes = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates.repeat(2),
            "sec": ["A", "B"] * n,
            "open": closes * 2,
            "high": [c + 0.5 for c in closes] * 2,
            "low": [c - 0.5 for c in closes] * 2,
            "close": closes * 2,
            "volume": [100] * (n * 2),
        }
    )


def test_discover_factors():
    discover_factors()
    names = list_factor_names()
    assert {"aroon_diff", "momentum_60_reversal", "ma60_slope_reversal", "low_vol_60", "money_flow_20"}.issubset(names)


def test_get_factor_class_no_params():
    cls = get_factor_class("aroon_diff")
    assert cls is not None
    factor = cls()
    assert factor.name == "aroon_diff"


def test_momentum_60_reversal_is_negated():
    """60-day momentum reversal output equals -pct_change(60)."""
    cls = get_factor_class("momentum_60_reversal")
    assert cls is not None
    factor = cls()
    prices = _long_price_data()
    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    close = prices.pivot(index="date", columns="sec", values="close")
    expected = -close.pct_change(periods=60, fill_method=None)
    expected.index.name = "date"
    pd.testing.assert_frame_equal(result, expected)


def test_low_vol_60_meta():
    cls = get_factor_class("low_vol_60")
    assert cls is not None
    assert cls.display_name == "60日低波动"
    assert cls.category == "波动"
    assert cls.direction == "positive"  # 因子层已取反，低波动得高分


def test_money_flow_20_meta():
    cls = get_factor_class("money_flow_20")
    assert cls is not None
    assert cls.display_name == "20日资金流方向"
    assert cls.category == "量能"
    assert cls.direction == "positive"


def test_factor_formula_present():
    for name in list_factor_names():
        cls = get_factor_class(name)
        assert cls is not None
        assert cls.formula, f"Factor {name} should have a formula"
        assert cls.description, f"Factor {name} should have a description"


def test_new_factors_build_on_generated_data():
    """Every new built-in factor builds a date x sec matrix."""
    prices = _long_price_data(n=80)
    for name in ["aroon_diff", "momentum_60_reversal", "ma60_slope_reversal", "low_vol_60", "money_flow_20"]:
        cls = get_factor_class(name)
        factor = cls().build(prices, pd.DataFrame(), ["A", "B"])
        assert factor.shape[1] == 2
        assert list(factor.columns) == ["A", "B"]
        assert factor.index.name == "date"