"""Tests for factor_service."""

from __future__ import annotations

import numpy as np
import pandas as pd

from webapp.services.factor_service import compute_factor, list_factors


def _make_price_data(n_dates: int = 60, n_sec: int = 10, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic price data for testing."""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    secs = [f"ETF{i:02d}" for i in range(n_sec)]

    rows = []
    base_prices = np.linspace(50, 150, n_sec)
    for i, date in enumerate(dates):
        for j, sec in enumerate(secs):
            drift = 0.0005 * (j - n_sec / 2)
            noise = np.random.randn() * 0.01
            base_prices[j] *= (1 + drift + noise)
            rows.append({
                "date": date,
                "sec": sec,
                "close": round(base_prices[j], 4),
            })
    return pd.DataFrame(rows)


def test_list_factors():
    factors = list_factors()
    assert len(factors) >= 3
    names = [f.name for f in factors]
    assert "momentum" in names
    assert "volatility" in names
    assert "reversal" in names

    # Check meta fields
    f = next(f for f in factors if f.name == "momentum")
    assert f.display_name == "动量因子"
    assert f.category == "动量"
    assert f.formula
    assert f.description
    assert f.direction == "positive"
    assert "window" in f.params_schema
    assert f.params_schema["window"].type == "int"
    assert f.params_schema["window"].default == 5
    assert f.params_schema["window"].label == "窗口天数"


def test_compute_factor_momentum():
    price_data = _make_price_data()
    universe = [f"ETF{i:02d}" for i in range(10)]

    result = compute_factor(
        factor_name="momentum",
        params={"window": 5},
        price_data=price_data,
        macro_data=pd.DataFrame(),
        universe=universe,
        horizon=5,
    )

    assert result.factor_name == "momentum"
    assert result.display_name == "动量因子"
    assert isinstance(result.ic_result.ic_mean, float)
    assert isinstance(result.ic_result.ic_std, float)
    assert isinstance(result.ic_result.icir, float)
    assert isinstance(result.ic_result.rank_ic_mean, float)
    assert isinstance(result.ic_result.rank_icir, float)
    assert len(result.ic_result.ic_series) > 0
    assert len(result.ic_result.rank_ic_series) > 0
    assert len(result.ic_result.icir_series) > 0

    # Group returns
    assert len(result.group_returns) == 5
    for g in result.group_returns:
        assert 1 <= g.group <= 5
        assert isinstance(g.annual_return, float)
        assert isinstance(g.cumulative_return, float)


def test_compute_factor_volatility():
    price_data = _make_price_data()
    universe = [f"ETF{i:02d}" for i in range(10)]

    result = compute_factor(
        factor_name="volatility",
        params={"window": 10, "annualization": 252},
        price_data=price_data,
        macro_data=pd.DataFrame(),
        universe=universe,
        horizon=5,
    )

    assert result.factor_name == "volatility"
    assert result.display_name == "波动率因子"
    assert len(result.ic_result.ic_series) > 0
    assert len(result.group_returns) == 5


def test_compute_factor_unknown_raises():
    import pytest
    price_data = _make_price_data()
    with pytest.raises(ValueError, match="Factor not found"):
        compute_factor(
            factor_name="nonexistent",
            params={},
            price_data=price_data,
            macro_data=pd.DataFrame(),
            universe=["ETF00"],
        )
