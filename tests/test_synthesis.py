from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.analysis import calculate_factor_ic, calculate_forward_returns, calculate_icir
from core.factors import MomentumFactor, VolatilityFactor
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
        "momentum_5": MomentumFactor(window=5).build(price_data, macro_data, universe),
        "volatility_20": VolatilityFactor(window=20).build(price_data, macro_data, universe),
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

