"""Tests for the optimizer factory."""

from __future__ import annotations

import pandas as pd
import pytest

from core.optimization.constraints import OptimizationConstraints
from core.optimization.equal_weight import EqualWeightOptimizer
from core.optimization.factory import OptimizerFactory, get_optimizer
from core.optimization.mvo import MVOptimizer


def test_factory_creates_equal_weight_optimizer() -> None:
    opt = OptimizerFactory.create("equal_weight", {"top_n": 3, "max_weight": 0.5})
    assert isinstance(opt, EqualWeightOptimizer)
    assert opt.top_n == 3
    assert opt.max_weight == 0.5


def test_factory_creates_mvo_optimizer() -> None:
    opt = OptimizerFactory.create(
        "mvo",
        {"objective": "min_variance", "max_weight": 0.4},
    )
    assert isinstance(opt, MVOptimizer)
    assert opt.objective == "min_variance"
    assert opt.max_weight == 0.4


def test_factory_default_params() -> None:
    opt = OptimizerFactory.create("equal_weight")
    assert opt.top_n == 5
    assert opt.max_weight == 0.5


def test_factory_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        OptimizerFactory.create("unknown_strategy")


def test_factory_case_insensitive() -> None:
    opt = OptimizerFactory.create("EQUAL_WEIGHT")
    assert isinstance(opt, EqualWeightOptimizer)


def test_factory_passes_constraints_to_mvo() -> None:
    constraints = OptimizationConstraints(single_max_weight=0.3)
    opt = OptimizerFactory.create(
        "mvo",
        {"objective": "min_variance"},
        constraints=constraints,
    )
    assert opt.constraints is constraints


def test_get_optimizer_convenience_function() -> None:
    opt = get_optimizer("equal_weight", {"top_n": 2})
    assert isinstance(opt, EqualWeightOptimizer)
    assert opt.top_n == 2


def test_factory_rejects_invalid_mvo_objective() -> None:
    with pytest.raises(ValueError):
        OptimizerFactory.create("mvo", {"objective": "bogus"})


def test_factory_creates_black_litterman() -> None:
    market_caps = pd.Series([100, 200], index=["A.SH", "B.SH"])
    cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.03]], index=["A.SH", "B.SH"], columns=["A.SH", "B.SH"])
    opt = OptimizerFactory.create_black_litterman(
        market_caps=market_caps,
        cov_matrix=cov,
        params={"max_weight": 0.6},
    )
    from core.optimization.bl import BLOptimizer

    assert isinstance(opt, BLOptimizer)
    assert opt.mvo.max_weight == 0.6


def test_factory_black_litterman_rejects_bad_objective() -> None:
    market_caps = pd.Series([100, 200], index=["A.SH", "B.SH"])
    cov = pd.DataFrame([[0.04, 0.01], [0.01, 0.03]], index=["A.SH", "B.SH"], columns=["A.SH", "B.SH"])
    with pytest.raises(ValueError):
        OptimizerFactory.create_black_litterman(
            market_caps=market_caps,
            cov_matrix=cov,
            params={"objective": "bogus"},
        )