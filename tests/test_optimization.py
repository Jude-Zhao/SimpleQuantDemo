from __future__ import annotations

import pandas as pd
import pytest

from core.analysis import calculate_factor_ic, calculate_forward_returns, calculate_icir
from core.factors import AroonDiffFactor, LowVol60Factor
from core.optimization import CategoryConstraint, EqualWeightOptimizer, OptimizationConstraints
from core.optimization.exceptions import OptimizationError
from core.synthesis import ICIRWeightedSynthesizer


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


def test_equal_weight_with_category_max_count() -> None:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7, 0.6, 0.5],
        index=["A.SH", "B.SH", "C.SH", "D.SH", "E.SH", "F.SH"],
    )
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
        "C.SH": {"category": "宽基"},
        "D.SH": {"category": "商品"},
        "E.SH": {"category": "商品"},
        "F.SH": {"category": "债券"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_count=2,
            )
        ]
    )

    weights = EqualWeightOptimizer(top_n=3, max_weight=1.0).optimize(
        scores,
        constraints=constraints,
        classifications=classifications,
    )

    selected = weights[weights > 0].index.tolist()
    assert "C.SH" not in selected
    assert "D.SH" in selected
    assert len(selected) == 3
    assert (weights > 0).sum() == 3


def test_equal_weight_with_category_min_count() -> None:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
    )
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
        "C.SH": {"category": "宽基"},
        "D.SH": {"category": "商品"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="商品",
                min_count=1,
            )
        ]
    )

    weights = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
        scores,
        constraints=constraints,
        classifications=classifications,
    )

    selected = weights[weights > 0].index.tolist()
    assert "D.SH" in selected
    assert len(selected) == 2


def test_equal_weight_with_category_min_count_infeasible() -> None:
    scores = pd.Series(
        [1.0, 0.9],
        index=["A.SH", "B.SH"],
    )
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="商品",
                min_count=2,
            )
        ]
    )

    with pytest.raises(OptimizationError):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
            scores,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_pipeline_with_example_synthesized_scores(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-06-03",
        end_date="2025-12-31",
    )
    factor_panel = {
        "momentum_5": AroonDiffFactor().build(price_data, macro_data, universe),
        "volatility_20": LowVol60Factor().build(price_data, macro_data, universe),
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
    synthesized = ICIRWeightedSynthesizer().synthesize(factor_panel, icir_data)
    latest_scores = synthesized.dropna(how="all").iloc[-1]

    weights = EqualWeightOptimizer(top_n=5, max_weight=0.5).optimize(latest_scores)

    assert weights.index.tolist() == universe
    assert weights.sum() == pytest.approx(1.0)
    assert (weights > 0).sum() == 5
    assert set(weights[weights > 0].unique()) == {0.2}