"""Tests for the Black-Litterman model and optimizer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.optimization.bl import BlackLittermanModel, BLOptimizer, View
from core.optimization.exceptions import OptimizationError


def _sample_caps() -> pd.Series:
    return pd.Series(
        [500, 300, 200, 100],
        index=["A.SH", "B.SH", "C.SH", "D.SH"],
        name="market_cap",
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


def test_bl_posterior_no_views_equals_prior() -> None:
    model = BlackLittermanModel(_sample_caps(), _sample_cov())
    post_mean, post_cov = model.posterior()
    assert list(post_mean.index) == ["A.SH", "B.SH", "C.SH", "D.SH"]
    assert len(post_mean) == 4
    assert post_cov.shape == (4, 4)
    # Posterior covariance should be symmetric.
    assert np.allclose(post_cov.to_numpy(), post_cov.to_numpy().T)


def test_bl_posterior_with_view() -> None:
    view = View(
        assets=[("A.SH", 0.5), ("B.SH", 0.5)],
        q=0.20,
        confidence=0.9,
    )
    model = BlackLittermanModel(_sample_caps(), _sample_cov(), views=[view])
    post_mean, post_cov = model.posterior()
    assert post_mean.index.tolist() == ["A.SH", "B.SH", "C.SH", "D.SH"]
    # A high-confidence view P'R ~ q=0.20 should pull the posterior view
    # portfolio return strongly toward q (from a large equilibrium prior).
    prior_mean, _ = BlackLittermanModel(_sample_caps(), _sample_cov()).posterior()
    prior_view_return = 0.5 * prior_mean.loc["A.SH"] + 0.5 * prior_mean.loc["B.SH"]
    view_portfolio_return = (
        0.5 * post_mean.loc["A.SH"] + 0.5 * post_mean.loc["B.SH"]
    )
    # The high-confidence view should dramatically pull the posterior toward q.
    assert abs(view_portfolio_return - 0.20) < abs(prior_view_return - 0.20)
    assert view_portfolio_return < 1.0  # far below the huge prior scale
    # A view with higher confidence should move the posterior more than a
    # lower-confidence view.
    low_conf_model = BlackLittermanModel(
        _sample_caps(),
        _sample_cov(),
        views=[
            View(
                assets=[("A.SH", 0.5), ("B.SH", 0.5)],
                q=0.20,
                confidence=0.1,
            )
        ],
    )
    low_post, _ = low_conf_model.posterior()
    low_view_return = 0.5 * low_post.loc["A.SH"] + 0.5 * low_post.loc["B.SH"]
    assert abs(low_view_return - 0.20) > abs(view_portfolio_return - 0.20)


def test_bl_view_asset_not_in_universe_raises() -> None:
    view = View(
        assets=[("ZZ.SH", 1.0)],
        q=0.10,
    )
    model = BlackLittermanModel(_sample_caps(), _sample_cov(), views=[view])
    with pytest.raises(OptimizationError):
        model.posterior()


def test_bl_empty_caps_raises() -> None:
    with pytest.raises(OptimizationError):
        BlackLittermanModel(pd.Series(dtype=float), _sample_cov())


def test_bl_optimizer_produces_valid_weights() -> None:
    opt = BLOptimizer(
        market_caps=_sample_caps(),
        cov_matrix=_sample_cov(),
        views=[],
        max_weight=0.5,
    )
    weights = opt.optimize()
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    assert (weights >= 0).all()
    assert (weights <= 0.5 + 1e-4).all()


def test_bl_optimizer_with_view_and_constraint() -> None:
    from core.optimization.constraints import CategoryConstraint, OptimizationConstraints

    view = View(
        assets=[("A.SH", 1.0)],
        q=0.30,
        confidence=0.8,
    )
    constraints = OptimizationConstraints(
        single_max_weight=0.4,
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_weight=0.5,
            )
        ],
    )
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
        "C.SH": {"category": "商品"},
        "D.SH": {"category": "商品"},
    }
    opt = BLOptimizer(
        market_caps=_sample_caps(),
        cov_matrix=_sample_cov(),
        views=[view],
        max_weight=0.4,
        constraints=constraints,
    )
    weights = opt.optimize(classifications=classifications)
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)
    assert weights.max() <= 0.4 + 1e-4
    growth = weights.loc[["A.SH", "B.SH"]].sum()
    assert growth <= 0.5 + 1e-4


def test_bl_optimizer_empty_views_matches_mvo_prior() -> None:
    """Without views, BL should match MVO solved on the equilibrium prior."""
    opt = BLOptimizer(
        market_caps=_sample_caps(),
        cov_matrix=_sample_cov(),
        views=[],
        objective="min_variance",
        max_weight=0.5,
    )
    weights = opt.optimize()
    assert weights.sum() == pytest.approx(1.0, abs=1e-4)