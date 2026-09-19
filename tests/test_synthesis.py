from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from core.analysis import (
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
    map_to_availability_dates,
)
from core.factors import MACDHistFactor, Drawdown120Factor
from core.synthesis import ICIRWeightedSynthesizer, calculate_decayed_icir_score
from core.synthesis.exceptions import SynthesisError


def _factor_panel() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2026-01-01", periods=3, freq="D", name="date")
    columns = ["A.SH", "B.SH"]
    return {
        "factor_a": pd.DataFrame([[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]], index=dates, columns=columns),
        "factor_b": pd.DataFrame([[10.0, 20.0], [20.0, 30.0], [30.0, 40.0]], index=dates, columns=columns),
    }


def _icir_data() -> dict[str, pd.Series]:
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    return {
        "factor_a": pd.Series([1.0, 1.0, 1.0], index=dates),
        "factor_b": pd.Series([3.0, 3.0, 3.0], index=dates),
    }


def test_calculate_decayed_icir_score_uses_half_life_weights() -> None:
    icir = pd.Series(
        [1.0, 2.0, 4.0],
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
    )

    score = calculate_decayed_icir_score(
        icir,
        as_of_date=pd.Timestamp("2026-01-03"),
        half_life_periods=1,
    )
    expected = (4.0 * 1.0 + 2.0 * 0.5 + 1.0 * 0.25) / (1.0 + 0.5 + 0.25)

    assert score == pytest.approx(expected)


def test_calculate_decayed_icir_score_ignores_future_values() -> None:
    icir = pd.Series(
        [1.0, 100.0],
        index=[pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")],
    )

    score = calculate_decayed_icir_score(
        icir,
        as_of_date=pd.Timestamp("2026-01-01"),
        half_life_periods=20,
    )

    assert score == pytest.approx(1.0)


def test_icir_weighted_synthesizer_combines_factors() -> None:
    result = ICIRWeightedSynthesizer(half_life_periods=20).synthesize(
        _factor_panel(),
        _icir_data(),
    )

    expected = _factor_panel()["factor_a"] * 0.25 + _factor_panel()["factor_b"] * 0.75

    pd.testing.assert_frame_equal(result, expected)


def test_icir_weighted_synthesizer_allows_negative_weights() -> None:
    panel = _factor_panel()
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    icir_data = {
        "factor_a": pd.Series([1.0, 1.0, 1.0], index=dates),
        "factor_b": pd.Series([-1.0, -1.0, -1.0], index=dates),
    }

    result = ICIRWeightedSynthesizer().synthesize(panel, icir_data)
    expected = panel["factor_a"] * 0.5 - panel["factor_b"] * 0.5

    pd.testing.assert_frame_equal(result, expected)


def test_icir_weighted_synthesizer_returns_nan_before_icir_available() -> None:
    panel = _factor_panel()
    icir_data = {
        "factor_a": pd.Series([1.0], index=[pd.Timestamp("2026-01-03")]),
        "factor_b": pd.Series([1.0], index=[pd.Timestamp("2026-01-03")]),
    }

    result = ICIRWeightedSynthesizer().synthesize(panel, icir_data)

    assert result.loc[pd.Timestamp("2026-01-01")].isna().all()
    assert result.loc[pd.Timestamp("2026-01-02")].isna().all()
    assert result.loc[pd.Timestamp("2026-01-03")].notna().all()


def test_icir_weighted_synthesizer_requires_icir_for_each_factor() -> None:
    with pytest.raises(SynthesisError):
        ICIRWeightedSynthesizer().synthesize(
            _factor_panel(),
            {"factor_a": _icir_data()["factor_a"]},
        )


def test_synthesis_pipeline_with_example_data(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )

    factor_panel = {
        "momentum_5": MACDHistFactor().build(price_data, macro_data, universe),
        "volatility_20": Drawdown120Factor().build(price_data, macro_data, universe),
    }
    forward_returns = calculate_forward_returns(price_data, horizon=5, universe=universe)
    icir_data = {
        factor_name: calculate_icir(
            calculate_factor_ic(factor, forward_returns, min_observations=10),
            window=20,
            min_periods=10,
        )
        for factor_name, factor in factor_panel.items()
    }

    synthesized = ICIRWeightedSynthesizer(half_life_periods=20).synthesize(factor_panel, icir_data)

    assert synthesized.shape == factor_panel["momentum_5"].shape
    assert synthesized.index.equals(factor_panel["momentum_5"].index)
    assert synthesized.columns.tolist() == universe
    assert int(synthesized.notna().sum().sum()) > 0
    assert np.isfinite(synthesized.dropna(how="all").to_numpy()).any()


def _three_sec_price_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    specs = (("A.SH", 1.001, 0.0), ("B.SH", 0.999, 1.3), ("C.SH", 1.0005, 2.1))
    for i, date in enumerate(dates):
        for sec, drift, phase in specs:
            rows.append({
                "date": date,
                "sec": sec,
                "close": 100.0 * drift ** i * (1.0 + 0.01 * math.sin(i + phase)),
            })
    return pd.DataFrame(rows)


def _static_panel(dates: pd.DatetimeIndex, universe: list[str]) -> dict[str, pd.DataFrame]:
    # 常数截面因子：合成分只随 ICIR 权重变化，从而隔离价格扰动的影响路径
    rows = {"f1": [1.0, 2.0, 3.0], "f2": [1.0, 3.0, 2.0]}
    return {
        name: pd.DataFrame(
            {sec: [v] * len(dates) for sec, v in zip(universe, row)}, index=dates
        )
        for name, row in rows.items()
    }


def test_icir_synthesis_no_lookahead_after_availability_shift() -> None:
    # 审计 F10 验收（日频）：修改 T 之后的价格不得改变 T 的已生成合成分
    dates = pd.bdate_range("2024-01-01", periods=30)
    universe = ["A.SH", "B.SH", "C.SH"]
    panel = _static_panel(dates, universe)

    def synthesize(prices: pd.DataFrame, shift: bool) -> pd.DataFrame:
        forward = calculate_forward_returns(prices, horizon=5, universe=universe)
        icir_data = {}
        for name, factor in panel.items():
            ic = calculate_factor_ic(factor, forward, min_observations=2)
            icir = calculate_icir(ic, window=3, min_periods=3)
            if shift:
                icir = map_to_availability_dates(icir, horizon=5, trading_dates=dates)
            icir_data[name] = icir
        return ICIRWeightedSynthesizer(half_life_periods=20).synthesize(panel, icir_data)

    base = _three_sec_price_frame(dates)
    perturbed = base.copy()
    # 各证券不同幅度扰动：统一倍数缩放只会让 forward 截面做仿射变换，
    # pearson IC 对仿射不变，将无法暴露泄漏
    for sec, mult in (("A.SH", 1.5), ("B.SH", 1.2), ("C.SH", 0.8)):
        mask = (perturbed["sec"] == sec) & (perturbed["date"] >= dates[12])
        perturbed.loc[mask, "close"] *= mult

    # 契约路径：T=dates[10] 只用标签 k≤4 的 ICIR（收益已在 dates[10] 前实现）
    assert np.isclose(
        synthesize(base, shift=True).loc[dates[10], "B.SH"],
        synthesize(perturbed, shift=True).loc[dates[10], "B.SH"],
    )
    # 旧拼法：标签日索引直接喂入，T 日权重内嵌 close[T+1..T+6] 的未来收益
    assert not np.isclose(
        synthesize(base, shift=False).loc[dates[10], "B.SH"],
        synthesize(perturbed, shift=False).loc[dates[10], "B.SH"],
    )


def test_icir_synthesis_sparse_rebalance_no_lookahead() -> None:
    # 审计 F10 验收（稀疏调仓 IC）：映射到收益实现日，而非稀疏样本机械右移
    dates = pd.bdate_range("2024-01-01", periods=30)
    universe = ["A.SH", "B.SH", "C.SH"]
    panel = _static_panel(dates, universe)
    rebalance_dates = dates[[0, 5, 10, 15, 20, 25]]

    def synthesize(prices: pd.DataFrame, shift: bool) -> pd.DataFrame:
        forward = calculate_forward_returns(prices, horizon=5, universe=universe)
        icir_data = {}
        for name, factor in panel.items():
            ic = calculate_factor_ic(factor, forward, min_observations=2)
            icir = calculate_icir(ic.reindex(rebalance_dates), window=2, min_periods=2)
            if shift:
                icir = map_to_availability_dates(icir, horizon=5, trading_dates=dates)
            icir_data[name] = icir
        return ICIRWeightedSynthesizer(half_life_periods=20).synthesize(panel, icir_data)

    base = _three_sec_price_frame(dates)
    perturbed = base.copy()
    # 同上：各证券不同幅度，避免 forward 截面仿射变换被 pearson IC 抹平
    for sec, mult in (("A.SH", 1.5), ("B.SH", 1.2), ("C.SH", 0.8)):
        mask = (perturbed["sec"] == sec) & (perturbed["date"] >= dates[12])
        perturbed.loc[mask, "close"] *= mult

    # 契约路径：T=dates[11] 只用标签 {0,5} 的 ICIR，其收益已在 dates[11] 前实现
    assert np.isclose(
        synthesize(base, shift=True).loc[dates[11], "B.SH"],
        synthesize(perturbed, shift=True).loc[dates[11], "B.SH"],
    )
    # 旧拼法：标签 10 的 ICIR 内嵌 dates[11..16] 收益，扰动后 T 日合成分可见变化
    assert not np.isclose(
        synthesize(base, shift=False).loc[dates[11], "B.SH"],
        synthesize(perturbed, shift=False).loc[dates[11], "B.SH"],
    )

