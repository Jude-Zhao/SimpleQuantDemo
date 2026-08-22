"""Tests for factor_service."""

from __future__ import annotations

import numpy as np
import pandas as pd

from webapp.services.factor_service import (
    compute_factor,
    list_factor_categories_meta,
)


def _make_price_data(n_dates: int = 120, n_sec: int = 10, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV price data for testing."""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    secs = [f"ETF{i:02d}" for i in range(n_sec)]

    rows = []
    base_prices = np.linspace(50, 150, n_sec)
    for date in dates:
        for j, sec in enumerate(secs):
            drift = 0.0005 * (j - n_sec / 2)
            noise = np.random.randn() * 0.01
            base_prices[j] *= (1 + drift + noise)
            close = round(base_prices[j], 4)
            rows.append({
                "date": date,
                "sec": sec,
                "open": round(close * 0.995, 4),
                "high": round(close * 1.01, 4),
                "low": round(close * 0.99, 4),
                "close": close,
                "volume": 1000,
            })
    return pd.DataFrame(rows)


def test_list_factors():
    cats = list_factor_categories_meta()
    factors = [f for cat in cats for f in cat.factors]
    assert len(factors) >= 3
    ids = [f.id for f in factors]
    assert "macd_hist" in ids
    assert "skewness_60_reversal" in ids
    assert "mfi" in ids

    # Check meta fields on a migrated factor instance
    f = next(f for f in factors if f.name == "macd_hist")
    assert f.id == "macd_hist"
    assert f.display_name == "MACD柱状图(归一化)"
    assert f.category == "动量"
    assert f.formula
    assert f.description
    assert f.direction == "positive"
    assert f.params == {}


def test_compute_factor_macd_hist():
    price_data = _make_price_data()
    universe = [f"ETF{i:02d}" for i in range(10)]

    result = compute_factor(
        factor_name="macd_hist",
        params={},
        price_data=price_data,
        macro_data=pd.DataFrame(),
        universe=universe,
        horizon=5,
    )

    assert result.factor_name == "macd_hist"
    assert result.display_name == "MACD柱状图(归一化)"
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


def test_compute_factor_mfi():
    price_data = _make_price_data()
    universe = [f"ETF{i:02d}" for i in range(10)]

    result = compute_factor(
        factor_name="mfi",
        params={},
        price_data=price_data,
        macro_data=pd.DataFrame(),
        universe=universe,
        horizon=5,
    )

    assert result.factor_name == "mfi"
    assert result.display_name == "14日资金流量指标"
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