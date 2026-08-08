"""Tests for the factor registry and auto-discovery mechanism."""

from __future__ import annotations

import pandas as pd

from core.factors.registry import (
    discover_factors,
    get_factor_class,
    get_factor_registry,
    list_factor_names,
)


def test_discover_factors():
    discover_factors()
    names = list_factor_names()
    assert "momentum" in names
    assert "volatility" in names
    assert "reversal" in names


def test_get_factor_class():
    cls = get_factor_class("momentum")
    assert cls is not None
    factor = cls(window=10)
    assert factor.name == "momentum_10"


def test_reversal_factor_auto_registered():
    names = list_factor_names()
    assert "reversal" in names
    cls = get_factor_class("reversal")
    assert cls is not None
    assert cls.display_name == "反转因子"
    assert cls.category == "价值"
    assert cls.direction == "positive"
    assert "window" in cls.params_schema


def test_reversal_factor_build():
    cls = get_factor_class("reversal")
    assert cls is not None
    factor = cls(window=5)

    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    prices = pd.DataFrame({
        "date": dates.repeat(2),
        "sec": ["A", "B"] * 10,
        "close": [100, 50, 101, 51, 102, 52, 103, 53, 104, 54,
                  105, 55, 106, 56, 107, 57, 108, 58, 109, 59],
    })

    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    assert result.shape[1] == 2
    assert list(result.columns) == ["A", "B"]
    # 反转因子是动量的相反数
    from core.factors.registry import get_factor_class as gfc
    mom_cls = gfc("momentum")
    mom = mom_cls(window=5)
    mom_result = mom.build(prices, pd.DataFrame(), ["A", "B"])
    # 非 NaN 值应该互为相反数
    common = result.dropna().index.intersection(mom_result.dropna().index)
    assert len(common) > 0
    pd.testing.assert_series_equal(
        result.loc[common, "A"],
        -mom_result.loc[common, "A"],
        check_names=False,
    )


def test_factor_meta_momentum():
    cls = get_factor_class("momentum")
    assert cls is not None
    assert cls.display_name == "动量因子"
    assert cls.category == "动量"
    assert cls.direction == "positive"
    assert "window" in cls.params_schema
    assert cls.params_schema["window"]["type"] == "int"
    assert cls.params_schema["window"]["default"] == 5
    assert cls.params_schema["window"]["label"] == "窗口天数"


def test_factor_meta_volatility():
    cls = get_factor_class("volatility")
    assert cls is not None
    assert cls.display_name == "波动率因子"
    assert cls.category == "波动率"
    assert cls.direction == "positive"  # 因子层已直接取反，低波动得高分
    assert "window" in cls.params_schema
    assert "annualization" in cls.params_schema


def test_factor_volatility_is_negated():
    """波动率因子输出取反：低波动标的得分更高。"""
    cls = get_factor_class("volatility")
    assert cls is not None
    factor = cls(window=3)

    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    # A 波动小（平稳上涨），B 波动大（剧烈震荡）
    prices = pd.DataFrame({
        "date": dates.repeat(2),
        "sec": ["A", "B"] * 6,
        "close": [100, 100, 101, 105, 102, 90, 103, 108, 104, 95, 105, 110],
    })

    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    last = result.iloc[-1].dropna()
    assert last["A"] > last["B"], "低波动 A 应得分更高（已取反）"


def test_factor_formula_present():
    for name in list_factor_names():
        cls = get_factor_class(name)
        assert cls is not None
        assert cls.formula, f"Factor {name} should have a formula"
        assert cls.description, f"Factor {name} should have a description"


def test_momentum_factor_still_works():
    """Ensure the refactored momentum factor still computes correctly."""
    cls = get_factor_class("momentum")
    assert cls is not None
    factor = cls(window=5)

    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    prices = pd.DataFrame({
        "date": dates.repeat(2),
        "sec": ["A", "B"] * 10,
        "close": [100, 50, 101, 51, 102, 52, 103, 53, 104, 54,
                  105, 55, 106, 56, 107, 57, 108, 58, 109, 59],
    })

    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    assert result.shape[1] == 2
    assert list(result.columns) == ["A", "B"]
    assert result.index.name == "date"
