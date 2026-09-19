from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.factors import (
    Drawdown120Factor,
    MACDHistFactor,
    MFIFactor,
    MomentumLinRegSlope,
    PSY20Factor,
    Skewness60ReversalFactor,
    VolumeOBVSlope,
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


def _with_nan(
    data: pd.DataFrame, sec: str, rows: list[int], fields: list[str]
) -> pd.DataFrame:
    """在指定标的长表的给定行位（0 起，负数从尾数）注入 NaN，返回副本。"""
    out = data.copy()
    out[fields] = out[fields].astype(float)
    block = out[out["sec"] == sec]
    for r in rows:
        out.loc[block.index[r], fields] = np.nan
    return out


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


def test_mfi_missing_day_yields_nan_not_finite() -> None:
    # 审计 F06 场景：持续上涨 + 末日 OHLC/量缺失，缺失不得洗成 1.0
    complete = MFIFactor().build(_long_price_data(n=25), _empty_macro(), _universe())
    assert np.isclose(complete["510300.SH"].iloc[-1], 1.0)

    data = _with_nan(
        _long_price_data(n=25), "510300.SH", [-1], ["high", "low", "close", "volume"]
    )
    factor = MFIFactor().build(data, _empty_macro(), _universe())
    assert pd.isna(factor["510300.SH"].iloc[-1])


def test_mfi_mid_series_nan_invalidates_window() -> None:
    # 单日缺失：当日与次日流量不可计算，14 日窗口滑过前分数均无效
    data = _with_nan(
        _long_price_data(n=40), "510300.SH", [20], ["high", "low", "close", "volume"]
    )
    factor = MFIFactor().build(data, _empty_macro(), _universe())["510300.SH"]

    assert factor.iloc[14:20].notna().all()
    assert factor.iloc[20:35].isna().all()
    assert factor.iloc[35:].notna().all()


def test_drawdown_120_missing_day_masked() -> None:
    # 审计 F06 场景：持续新高 + 末日 close 缺失，缺失不得继承历史回撤 0.0
    complete = Drawdown120Factor().build(
        _long_price_data(n=25), _empty_macro(), _universe()
    )
    assert complete["510300.SH"].iloc[-1] == 0.0

    data = _with_nan(_long_price_data(n=25), "510300.SH", [-1], ["close"])
    factor = Drawdown120Factor().build(data, _empty_macro(), _universe())
    assert pd.isna(factor["510300.SH"].iloc[-1])


def test_drawdown_120_suspension_gap() -> None:
    # 停牌段：缺失日 NaN 不继承历史回撤，复牌日按可用观测恢复到与完整数据一致
    base = _long_price_data(n=40)
    base.loc[base[base["sec"] == "510300.SH"].index[30], "close"] = 10.0
    complete = Drawdown120Factor().build(base, _empty_macro(), _universe())["510300.SH"]

    data = _with_nan(base, "510300.SH", [34, 35, 36], ["close"])
    factor = Drawdown120Factor().build(data, _empty_macro(), _universe())["510300.SH"]

    dd30 = complete.iloc[30]
    assert dd30 < 0
    assert np.isclose(complete.iloc[34:37], dd30).all()
    assert factor.iloc[34:37].isna().all()
    assert np.isclose(factor.iloc[37], dd30)


def test_drawdown_120_pre_listing_rows_stay_nan() -> None:
    # 预上市：无价格期间因子不得产出，首个有效价日起正常
    data = _with_nan(
        _long_price_data(n=30), "159928.SZ", list(range(10)), ["high", "low", "close"]
    )
    factor = Drawdown120Factor().build(data, _empty_macro(), _universe())["159928.SZ"]

    assert factor.iloc[:10].isna().all()
    assert factor.notna().iloc[10:].all()


def test_volume_obv_slope_missing_day_yields_nan() -> None:
    # 输入缺失不再 ffill/置零、输出不再 ffill(limit=5)：缺失段分数无效且不延续旧值
    data = _with_nan(_long_price_data(n=40), "510300.SH", [25], ["close", "volume"])
    factor = VolumeOBVSlope(window=5).build(data, _empty_macro(), _universe())[
        "510300.SH"
    ]

    assert factor.iloc[5:25].notna().all()
    assert factor.iloc[25:31].isna().all()
    assert factor.iloc[31:].notna().all()


def test_momentum_linreg_slope_missing_day_yields_nan() -> None:
    # 输入缺失不再 ffill、输出不再 ffill(limit=5)：缺失段分数无效且不延续旧值
    data = _with_nan(_long_price_data(n=40), "510300.SH", [25], ["close"])
    factor = MomentumLinRegSlope(window=5).build(data, _empty_macro(), _universe())[
        "510300.SH"
    ]

    assert factor.iloc[5:25].notna().all()
    assert factor.iloc[25:31].isna().all()
    assert factor.iloc[31:].notna().all()