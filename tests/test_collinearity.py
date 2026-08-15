from __future__ import annotations

import pandas as pd
import pytest

from core.analysis import (
    analyze_collinearity,
    calculate_factor_correlation_matrix,
    find_correlated_pairs,
)
from core.analysis.exceptions import AnalysisError
from core.factors import AroonDiffFactor, LowVol60Factor


def _factor_panel() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    base = pd.DataFrame(
        [
            [1.0, 2.0, 3.0],
            [2.0, 3.0, 4.0],
            [3.0, 4.0, 5.0],
            [4.0, 5.0, 6.0],
        ],
        index=pd.DatetimeIndex(dates, name="date"),
        columns=["A.SH", "B.SH", "C.SH"],
    )
    inverse = -base
    independent = pd.DataFrame(
        [
            [1.0, 3.0, 2.0],
            [3.0, 1.0, 2.0],
            [2.0, 1.0, 3.0],
            [3.0, 2.0, 1.0],
        ],
        index=base.index,
        columns=base.columns,
    )
    return {
        "base": base,
        "same": base * 2,
        "inverse": inverse,
        "independent": independent,
    }


def _icir_data() -> dict[str, pd.Series]:
    index = pd.date_range("2026-01-01", periods=3, freq="D")
    return {
        "base": pd.Series([0.1, 0.2, 0.3], index=index),
        "same": pd.Series([0.1, 0.4, 0.8], index=index),
        "inverse": pd.Series([0.1, 0.2, 0.1], index=index),
        "independent": pd.Series([0.1, 0.2, 0.2], index=index),
    }


def test_calculate_factor_correlation_matrix() -> None:
    corr = calculate_factor_correlation_matrix(_factor_panel(), min_observations=3)

    assert corr.loc["base", "same"] == pytest.approx(1.0)
    assert corr.loc["base", "inverse"] == pytest.approx(-1.0)
    assert corr.loc["base", "base"] == pytest.approx(1.0)


def test_find_correlated_pairs_uses_absolute_correlation() -> None:
    corr = calculate_factor_correlation_matrix(_factor_panel(), min_observations=3)
    pairs = find_correlated_pairs(corr, threshold=0.9)
    pair_keys = {(pair.factor_a, pair.factor_b) for pair in pairs}

    assert ("base", "same") in pair_keys
    assert ("base", "inverse") in pair_keys
    assert all(abs(pair.correlation) > 0.9 for pair in pairs)


def test_analyze_collinearity_warn_mode_keeps_all_factors() -> None:
    panel = _factor_panel()

    result = analyze_collinearity(panel, threshold=0.9, mode="warn")

    assert list(result.selected_factor_panel) == list(panel)
    assert result.dropped_factors == []
    assert len(result.correlated_pairs) >= 2
    assert "相关系数" in result.warnings[0]


def test_analyze_collinearity_select_mode_keeps_highest_latest_icir() -> None:
    result = analyze_collinearity(
        _factor_panel(),
        icir_data=_icir_data(),
        threshold=0.9,
        mode="select",
    )

    assert "same" in result.selected_factor_panel
    assert "independent" in result.selected_factor_panel
    assert "base" in result.dropped_factors
    assert "inverse" in result.dropped_factors


def test_analyze_collinearity_select_mode_requires_icir_data() -> None:
    with pytest.raises(AnalysisError):
        analyze_collinearity(_factor_panel(), threshold=0.9, mode="select")


def test_analyze_collinearity_keeps_all_when_no_pairs() -> None:
    result = analyze_collinearity(
        _factor_panel(),
        icir_data=_icir_data(),
        threshold=0.9,
        mode="select",
        min_observations=999,
    )

    assert list(result.selected_factor_panel) == list(_factor_panel())
    assert result.dropped_factors == []
    assert result.correlated_pairs == []


def test_collinearity_pipeline_with_example_factors(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )
    momentum = AroonDiffFactor().build(price_data, macro_data, universe)
    volatility = LowVol60Factor().build(price_data, macro_data, universe)

    result = analyze_collinearity(
        {"momentum_5": momentum, "volatility_20": volatility},
        threshold=0.7,
        mode="warn",
        min_observations=10,
    )

    assert result.correlation_matrix.shape == (2, 2)
    assert list(result.selected_factor_panel) == ["momentum_5", "volatility_20"]
    assert set(result.correlation_matrix.columns) == {"momentum_5", "volatility_20"}
