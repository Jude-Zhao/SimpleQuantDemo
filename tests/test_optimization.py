from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.analysis import calculate_factor_ic, calculate_forward_returns, calculate_icir
from core.data import CsvDataSource
from core.factors import MomentumFactor, VolatilityFactor
from core.optimization import EqualWeightOptimizer
from core.optimization.exceptions import OptimizationError
from core.synthesis import ICIRWeightedSynthesizer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_EXAMPLE = PROJECT_ROOT / "data_example"


def _example_paths() -> tuple[Path, Path, Path]:
    csv_paths = sorted(
        [path for path in DATA_EXAMPLE.iterdir() if path.suffix.lower() == ".csv"],
        key=lambda path: path.stat().st_size,
    )
    return (
        csv_paths[-1],
        csv_paths[0],
        next(path for path in DATA_EXAMPLE.iterdir() if path.suffix.lower() == ".xlsx"),
    )


def test_equal_weight_optimizer_selects_top_n() -> None:
    scores = pd.Series(
        [0.1, 0.4, 0.3, 0.2],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
        name="score",
    )

    weights = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(scores)

    assert weights.to_dict() == {
        "A.SH": 0.0,
        "B.SH": 0.5,
        "C.SH": 0.5,
        "D.SH": 0.0,
    }
    assert weights.sum() == pytest.approx(1.0)


def test_equal_weight_optimizer_tie_breaks_by_security_code() -> None:
    scores = pd.Series(
        [1.0, 1.0, 0.5],
        index=["B.SH", "A.SH", "C.SH"],
    )

    weights = EqualWeightOptimizer(top_n=1, max_weight=1.0).optimize(scores)

    assert weights.loc["A.SH"] == pytest.approx(1.0)
    assert weights.loc["B.SH"] == pytest.approx(0.0)


def test_equal_weight_optimizer_ignores_nan_scores() -> None:
    scores = pd.Series(
        [1.0, None, 0.5],
        index=["A.SH", "B.SH", "C.SH"],
    )

    weights = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(scores)

    assert weights.loc["A.SH"] == pytest.approx(0.5)
    assert weights.loc["C.SH"] == pytest.approx(0.5)
    assert weights.loc["B.SH"] == pytest.approx(0.0)


def test_equal_weight_optimizer_rejects_unfillable_constraints() -> None:
    with pytest.raises(OptimizationError):
        EqualWeightOptimizer(top_n=1, max_weight=0.5)


def test_equal_weight_optimizer_rejects_not_enough_eligible_scores() -> None:
    scores = pd.Series([1.0, None], index=["A.SH", "B.SH"])

    with pytest.raises(OptimizationError):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(scores)


def test_equal_weight_optimizer_rejects_duplicate_scores() -> None:
    scores = pd.Series([1.0, 0.5], index=["A.SH", "A.SH"])

    with pytest.raises(OptimizationError):
        EqualWeightOptimizer(top_n=1, max_weight=1.0).optimize(scores)


def test_equal_weight_pipeline_with_example_synthesized_scores() -> None:
    etf_path, macro_path, universe_path = _example_paths()
    source = CsvDataSource(etf_path, macro_path, universe_path)
    price_data, macro_data, universe = source.load_all(
        start_date="2025-06-02",
        end_date="2026-03-13",
    )
    factor_panel = {
        "momentum_5": MomentumFactor(window=5).build(price_data, macro_data, universe),
        "volatility_20": VolatilityFactor(window=20).build(price_data, macro_data, universe),
    }
    forward_returns = calculate_forward_returns(price_data, horizon=5, universe=universe)
    icir_data = {
        factor_name: calculate_icir(
            calculate_factor_ic(factor, forward_returns, min_periods=10),
            window=20,
            min_periods=10,
        )
        for factor_name, factor in factor_panel.items()
    }
    synthesized = ICIRWeightedSynthesizer().synthesize(factor_panel, icir_data)
    latest_scores = synthesized.dropna(how="all").iloc[-1]

    weights = EqualWeightOptimizer(top_n=5, max_weight=0.5).optimize(latest_scores)

    assert weights.index.tolist() == universe
    assert weights.sum() == pytest.approx(1.0)
    assert (weights > 0).sum() == 5
    assert set(weights[weights > 0].unique()) == {0.2}

