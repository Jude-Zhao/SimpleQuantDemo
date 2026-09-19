"""Tests for factor_service."""

from __future__ import annotations

import numpy as np
import pandas as pd

from webapp.services.factor_service import (
    _calculate_group_returns,
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


def test_group_returns_constant_daily_compounding():
    # 审计 F08 验收：常数日收益下累计收益等于底层日收益复利，不再重叠重复计数
    # （旧实现 8 个重叠 5 日窗口会报 1.01^40-1 ≈ 47.1%）
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    sec = "ETF00"
    close = pd.DataFrame({sec: 100.0 * 1.01 ** np.arange(10)}, index=dates)
    factor = pd.DataFrame({sec: np.arange(10, dtype=float)}, index=dates)

    result = _calculate_group_returns(factor, close, n_groups=1)

    assert np.isclose(result[0].cumulative_return, 1.01 ** 8 - 1)  # 持仓日 T2..T9 共 8 日
    assert np.isclose(result[0].annual_return, 1.01 ** 252 - 1)    # 年化口径与实际日收益一致


def test_group_returns_no_overlap_double_counting():
    # 变动日收益：累计收益必须等于底层持仓日日收益的精确复利
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    sec = "ETF00"
    closes = [100.0, 102.0, 100.98, 104.0094, 104.529447, 102.438858, 103.463246, 107.561776]
    close = pd.DataFrame({sec: closes}, index=dates)
    factor = pd.DataFrame({sec: np.arange(8, dtype=float)}, index=dates)

    result = _calculate_group_returns(factor, close, n_groups=1)

    ratios = [closes[t] / closes[t - 1] for t in range(2, 8)]  # 持仓日 T2..T7
    assert np.isclose(result[0].cumulative_return, float(np.prod(ratios)) - 1)


def test_group_returns_high_factor_group_outperforms():
    # 因子与日收益正相关：逐日调仓下高分组累计收益应高于低分组
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    secs = ["ETF00", "ETF01", "ETF02", "ETF03"]
    close = pd.DataFrame(
        {s: 100.0 * np.cumprod(np.full(12, 1 + 0.001 * i)) for i, s in enumerate(secs)},
        index=dates,
    )
    factor = pd.DataFrame({s: float(i) for i, s in enumerate(secs)}, index=dates)

    result = _calculate_group_returns(factor, close, n_groups=2)

    high = next(r for r in result if r.group == 2)
    low = next(r for r in result if r.group == 1)
    assert high.cumulative_return > low.cumulative_return


def test_group_returns_t_plus_one_alignment():
    # 仅首日有信号：资金 T+1 收盘进场，首段收益是 T+2 持仓日的日收益（不吃 T+1 当日）
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    sec = "ETF00"
    close = pd.DataFrame({sec: [100.0, 101.0, 106.05, 108.171, 108.171]}, index=dates)
    factor = pd.DataFrame({sec: [1.0, np.nan, np.nan, np.nan, np.nan]}, index=dates)

    result = _calculate_group_returns(factor, close, n_groups=1)

    assert np.isclose(result[0].cumulative_return, 0.05)