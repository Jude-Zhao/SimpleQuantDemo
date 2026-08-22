"""Tests for the factor registry and auto-discovery mechanism."""

from __future__ import annotations

import pandas as pd

from core.factors.registry import (
    discover_factors,
    get_factor_class,
    list_factor_names,
)

MIGRATED = {"macd_hist", "skewness_60_reversal", "mfi", "psy20", "drawdown_120"}


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
    assert MIGRATED.issubset(names)


def test_get_factor_class_no_params():
    cls = get_factor_class("macd_hist")
    assert cls is not None
    factor = cls()
    assert factor.name == "macd_hist"


def test_psy20_formula():
    """20-day psychological line equals mean(ret>0) over a 20-day window."""
    cls = get_factor_class("psy20")
    assert cls is not None
    factor = cls()
    prices = _long_price_data(n=80)
    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    close = prices.pivot(index="date", columns="sec", values="close")
    ret = close.pct_change(fill_method=None)
    expected = (ret > 0.0).where(ret.notna()).rolling(20).mean()
    expected.index.name = "date"
    pd.testing.assert_frame_equal(result, expected)


def test_drawdown_120_formula():
    """120-day max drawdown equals rolling min of close/cummax - 1."""
    cls = get_factor_class("drawdown_120")
    assert cls is not None
    factor = cls()
    prices = _long_price_data(n=80)
    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    close = prices.pivot(index="date", columns="sec", values="close")
    dd = close / close.cummax() - 1.0
    expected = dd.rolling(120, min_periods=1).min()
    expected.index.name = "date"
    pd.testing.assert_frame_equal(result, expected)


def test_skewness_60_reversal_meta():
    cls = get_factor_class("skewness_60_reversal")
    assert cls is not None
    assert cls.display_name == "60日偏度反转"
    assert cls.category == "反转"
    assert cls.direction == "positive"  # 因子层已取反，右偏(过去大涨脉冲)得低分


def test_mfi_meta():
    cls = get_factor_class("mfi")
    assert cls is not None
    assert cls.display_name == "14日资金流量指标"
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
    prices = _long_price_data(n=140)
    for name in ["macd_hist", "skewness_60_reversal", "mfi", "psy20", "drawdown_120"]:
        cls = get_factor_class(name)
        factor = cls().build(prices, pd.DataFrame(), ["A", "B"])
        assert factor.shape[1] == 2
        assert list(factor.columns) == ["A", "B"]
        assert factor.index.name == "date"