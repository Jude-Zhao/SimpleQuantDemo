"""Tests for the mean-variance portfolio optimizer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.optimization import (
    CategoryConstraint,
    OptimizationConstraints,
)
from core.optimization.exceptions import OptimizationError
from core.optimization.mvo import MVOptimizer


def _sample_returns() -> pd.Series:
    return pd.Series(
        [0.10, 0.08, 0.12, 0.06],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
        name="expected_return",
    )


def _sample_cov() -> pd.DataFrame:
    cov = pd.DataFrame(
        [
            [0.04, 0.01, 0.005, 0.002],
            [0.01, 0.03, 0.008, 0.001],
            [0.005, 0.008, 0.05, 0.004],
            [0.002, 0.001, 0.004, 0.02],
        ],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
        columns=["A.SH", "B.SH", "C.SH", "D.SH"],
    )
    return cov


def test_min_variance_weights_sum_to_one() -> None:
    opt = MVOptimizer(objective="min_variance", max_weight=0.5)
    weights = opt.optimize(_sample_returns(), _sample_cov())
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    assert (weights >= 0).all()
    assert (weights <= 0.5 + 1e-4).all()


def test_max_sharpe_produces_positive_weights() -> None:
    opt = MVOptimizer(objective="max_sharpe", max_weight=0.5)
    weights = opt.optimize(_sample_returns(), _sample_cov())
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    assert (weights >= -1e-4).all()


def test_target_return_objective() -> None:
    opt = MVOptimizer(
        objective="target_return",
        target_return=0.08,
        max_weight=0.5,
    )
    weights = opt.optimize(_sample_returns(), _sample_cov())
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    # Portfolio expected return should at least approach target
    port_return = float(weights @ _sample_returns())
    assert port_return >= 0.07


def test_empty_returns_raises() -> None:
    opt = MVOptimizer(objective="min_variance")
    with pytest.raises(OptimizationError):
        opt.optimize(pd.Series(dtype=float), _sample_cov())


def test_invalid_objective_raises() -> None:
    with pytest.raises(ValueError):
        MVOptimizer(objective="invalid")  # type: ignore[arg-type]


def test_target_return_requires_value() -> None:
    with pytest.raises(ValueError):
        MVOptimizer(objective="target_return", target_return=None)


def test_invalid_weight_bounds_raise() -> None:
    with pytest.raises(ValueError):
        MVOptimizer(objective="min_variance", min_weight=0.6, max_weight=0.5)


def test_duplicate_returns_raise() -> None:
    rets = pd.Series([0.1, 0.2], index=["A.SH", "A.SH"])
    opt = MVOptimizer(objective="min_variance")
    with pytest.raises(OptimizationError):
        opt.optimize(rets, _sample_cov())


def test_category_constraint_honored() -> None:
    """A category max_weight should be respected by the optimizer."""
    rets = pd.Series(
        [0.15, 0.14, 0.05, 0.04],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
    )
    cov = pd.DataFrame(
        [
            [0.05, 0.01, 0.01, 0.0],
            [0.01, 0.045, 0.01, 0.0],
            [0.01, 0.01, 0.06, 0.0],
            [0.0, 0.0, 0.0, 0.04],
        ],
        index=rets.index,
        columns=rets.index,
    )
    classifications = {
        "A.SH": {"category": "成长"},
        "B.SH": {"category": "成长"},
        "C.SH": {"category": "价值"},
        "D.SH": {"category": "价值"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="成长",
                max_weight=0.4,
            )
        ]
    )
    opt = MVOptimizer(objective="max_sharpe", max_weight=0.5, constraints=constraints)
    weights = opt.optimize(
        rets,
        cov,
        classifications=classifications,
    )

    growth_weight = weights.loc[["A.SH", "B.SH"]].sum()
    assert growth_weight <= 0.4 + 1e-4
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)


def test_min_weight_bound_respected() -> None:
    rets = _sample_returns()
    opt = MVOptimizer(objective="min_variance", max_weight=0.5, min_weight=0.1)
    weights = opt.optimize(rets, _sample_cov())
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    assert (weights >= 0.1 - 1e-4).all()