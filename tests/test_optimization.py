from __future__ import annotations

import pandas as pd
import pytest

from core.analysis import calculate_factor_ic, calculate_forward_returns, calculate_icir
from core.factors import MACDHistFactor, Drawdown120Factor
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


def _xy_scores_and_classifications() -> tuple[pd.Series, dict[str, dict[str, str]]]:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7],
        index=["X1.SH", "X2.SH", "Y1.SH", "Y2.SH"],
    )
    classifications = {
        "X1.SH": {"category": "x"},
        "X2.SH": {"category": "x"},
        "Y1.SH": {"category": "y"},
        "Y2.SH": {"category": "y"},
    }
    return scores, classifications


def test_equal_weight_rejects_sum_of_min_counts_exceeding_top_n() -> None:
    scores, classifications = _xy_scores_and_classifications()
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(category_key="category", category_value="x", min_count=2),
            CategoryConstraint(category_key="category", category_value="y", min_count=2),
        ]
    )

    with pytest.raises(OptimizationError, match="exceeds top_n=2"):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
            scores,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_rejects_min_count_above_max_count() -> None:
    scores, classifications = _xy_scores_and_classifications()
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="x",
                min_count=3,
                max_count=2,
            )
        ]
    )

    with pytest.raises(OptimizationError, match=r"min_count=3 > max_count=2"):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
            scores,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_rejects_must_include_violating_category_max() -> None:
    scores, classifications = _xy_scores_and_classifications()
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(category_key="category", category_value="x", min_count=2),
            CategoryConstraint(category_key="category", category_value="x", max_count=1),
        ]
    )

    with pytest.raises(OptimizationError, match="max_count=1"):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
            scores,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_rejects_unsupported_category_count_key() -> None:
    scores, classifications = _xy_scores_and_classifications()
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="asset_type",
                category_value="etf",
                min_count=1,
            )
        ]
    )

    with pytest.raises(OptimizationError, match="asset_type"):
        EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
            scores,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_ignores_weight_only_constraints_for_other_keys() -> None:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
    )
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="asset_type",
                category_value="etf",
                max_weight=0.5,
            )
        ]
    )

    weights = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
        scores,
        constraints=constraints,
        classifications={"A.SH": {"asset_type": "etf"}},
    )

    # Count-less constraints on other category keys stay out of scope for the
    # optimizer; weight bounds are validated by validate_constraints.
    assert weights.loc["A.SH"] == pytest.approx(0.5)


def test_equal_weight_feasible_constraints_respected() -> None:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
        index=["X1.SH", "X2.SH", "X3.SH", "Y1.SH", "Y2.SH", "Y3.SH", "Z1.SH"],
    )
    classifications = {
        "X1.SH": {"category": "x"},
        "X2.SH": {"category": "x"},
        "X3.SH": {"category": "x"},
        "Y1.SH": {"category": "y"},
        "Y2.SH": {"category": "y"},
        "Y3.SH": {"category": "y"},
        "Z1.SH": {"category": "z"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="x",
                min_count=1,
                max_count=2,
            ),
            CategoryConstraint(category_key="category", category_value="y", min_count=1),
        ]
    )

    weights = EqualWeightOptimizer(top_n=4, max_weight=0.25).optimize(
        scores,
        constraints=constraints,
        classifications=classifications,
    )

    selected = weights[weights > 0].index.tolist()
    assert len(selected) == 4
    assert weights.sum() == pytest.approx(1.0)
    x_count = sum(1 for sec in selected if classifications[sec]["category"] == "x")
    y_count = sum(1 for sec in selected if classifications[sec]["category"] == "y")
    assert 1 <= x_count <= 2
    assert y_count >= 1
    assert weights.to_dict() == {
        "X1.SH": 0.25,
        "X2.SH": 0.25,
        "X3.SH": 0.0,
        "Y1.SH": 0.25,
        "Y2.SH": 0.25,
        "Y3.SH": 0.0,
        "Z1.SH": 0.0,
    }


def test_equal_weight_unconstrained_result_unchanged() -> None:
    scores = pd.Series(
        [0.1, 0.4, 0.3, 0.2],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
    )
    base = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(scores)

    weight_only = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(category_key="category", category_value="宽基", min_weight=0.1)
        ]
    )
    with_constraints = EqualWeightOptimizer(top_n=2, max_weight=0.5).optimize(
        scores,
        constraints=weight_only,
        classifications={"A.SH": {"category": "宽基"}},
    )

    assert base.to_dict() == {"A.SH": 0.0, "B.SH": 0.5, "C.SH": 0.5, "D.SH": 0.0}
    assert with_constraints.to_dict() == base.to_dict()


def test_equal_weight_duplicate_constraints_take_strictest() -> None:
    scores = pd.Series(
        [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.45, 0.4],
        index=["X1.SH", "X2.SH", "X3.SH", "X4.SH", "Y1.SH", "Y2.SH", "Y3.SH", "Z1.SH"],
    )
    classifications = {
        "X1.SH": {"category": "x"},
        "X2.SH": {"category": "x"},
        "X3.SH": {"category": "x"},
        "X4.SH": {"category": "x"},
        "Y1.SH": {"category": "y"},
        "Y2.SH": {"category": "y"},
        "Y3.SH": {"category": "y"},
        "Z1.SH": {"category": "z"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(category_key="category", category_value="x", max_count=1),
            CategoryConstraint(
                category_key="category",
                category_value="x",
                min_count=1,
                max_count=3,
            ),
            CategoryConstraint(category_key="category", category_value="y", min_count=1),
            CategoryConstraint(category_key="category", category_value="y", min_count=2),
        ]
    )

    weights = EqualWeightOptimizer(top_n=4, max_weight=0.25).optimize(
        scores,
        constraints=constraints,
        classifications=classifications,
    )

    selected = set(weights[weights > 0].index)
    assert len(selected) == 4
    # x: max_count keeps the minimum (1 of {1, 3}) -> only the best x security.
    assert sum(1 for sec in selected if classifications[sec]["category"] == "x") == 1
    assert "X1.SH" in selected
    # y: min_count keeps the maximum (2 of {1, 2}) -> the two best y securities
    # are must-include; the last slot goes to the next best y (y has no max).
    assert sum(1 for sec in selected if classifications[sec]["category"] == "y") == 3
    assert {"Y1.SH", "Y2.SH"} <= selected
    assert weights.to_dict() == {
        "X1.SH": 0.25,
        "X2.SH": 0.0,
        "X3.SH": 0.0,
        "X4.SH": 0.0,
        "Y1.SH": 0.25,
        "Y2.SH": 0.25,
        "Y3.SH": 0.25,
        "Z1.SH": 0.0,
    }


def test_equal_weight_post_verification_catches_violation() -> None:
    """The final re-verification catches selections that bypass pre-checks.

    ``optimize`` rejects duplicate scores upstream; calling the selection
    routine directly with a duplicated ranked row collapses the must-include
    set below ``min_count``, which only the post-selection verification can
    catch.
    """
    ranked = pd.DataFrame(
        {"sec": ["X1.SH", "X1.SH", "X2.SH", "Y1.SH"], "score": [1.0, 1.0, 0.9, 0.8]}
    )
    classifications = {
        "X1.SH": {"category": "x"},
        "X2.SH": {"category": "x"},
        "Y1.SH": {"category": "y"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(category_key="category", category_value="x", min_count=3),
        ]
    )

    with pytest.raises(OptimizationError, match="min_count=3"):
        EqualWeightOptimizer._select_securities(
            ranked=ranked,
            top_n=3,
            constraints=constraints,
            classifications=classifications,
        )


def test_equal_weight_pipeline_with_example_synthesized_scores(sqlite_source) -> None:
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
    synthesized = ICIRWeightedSynthesizer().synthesize(factor_panel, icir_data)
    latest_scores = synthesized.dropna(how="all").iloc[-1]

    weights = EqualWeightOptimizer(top_n=5, max_weight=0.5).optimize(latest_scores)

    assert weights.index.tolist() == universe
    assert weights.sum() == pytest.approx(1.0)
    assert (weights > 0).sum() == 5
    assert set(weights[weights > 0].unique()) == {0.2}