"""Optimizer factory providing a unified interface for all optimizers."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core.optimization.base import PortfolioOptimizer
from core.optimization.constraints import OptimizationConstraints
from core.optimization.equal_weight import EqualWeightOptimizer
from core.optimization.mvo import MVOptimizer, ObjectiveType


class OptimizerFactory:
    """Create portfolio optimizers by strategy type.

    Strategy types:
        - ``equal_weight``: Top-N equal-weight selection (linear-factor strategy).
        - ``mvo``: Mean-variance optimizer.
        - ``bl``: Black-Litterman optimizer.
    """

    @staticmethod
    def create(
        strategy_type: str,
        params: dict[str, Any] | None = None,
        constraints: OptimizationConstraints | None = None,
    ) -> PortfolioOptimizer:
        params = params or {}
        strategy_type = strategy_type.lower()

        if strategy_type == "equal_weight":
            return EqualWeightOptimizer(
                top_n=int(params.get("top_n", 5)),
                max_weight=float(params.get("max_weight", 0.5)),
                min_weight=float(params.get("min_weight", 0.0)),
            )

        if strategy_type == "mvo":
            objective = params.get("objective", "max_sharpe")
            if objective not in ("min_variance", "max_sharpe", "target_return"):
                raise ValueError(f"Unknown MVO objective: {objective}")
            return MVOptimizer(
                objective=objective,
                max_weight=float(params.get("max_weight", 0.5)),
                min_weight=float(params.get("min_weight", 0.0)),
                risk_free_rate=float(params.get("risk_free_rate", 0.0)),
                target_return=params.get("target_return"),
                constraints=constraints,
            )

        raise ValueError(f"Unknown strategy type: {strategy_type}")

    @staticmethod
    def create_black_litterman(
        market_caps: pd.Series,
        cov_matrix: pd.DataFrame,
        views: list[Any] | None = None,
        params: dict[str, Any] | None = None,
        constraints: OptimizationConstraints | None = None,
    ) -> Any:
        """Create a Black-Litterman optimizer (needs market data inputs)."""
        from core.optimization.bl import BLOptimizer

        params = params or {}
        objective = params.get("objective", "max_sharpe")
        if objective not in ("min_variance", "max_sharpe", "target_return"):
            raise ValueError(f"Unknown BL objective: {objective}")

        return BLOptimizer(
            market_caps=market_caps,
            cov_matrix=cov_matrix,
            views=views,
            tau=float(params.get("tau", 0.05)),
            objective=objective,
            max_weight=float(params.get("max_weight", 0.5)),
            min_weight=float(params.get("min_weight", 0.0)),
            constraints=constraints,
        )


def get_optimizer(
    strategy_type: str,
    params: dict[str, Any] | None = None,
    constraints: OptimizationConstraints | None = None,
    **kwargs: Any,
) -> PortfolioOptimizer:
    """Convenience function for creating an optimizer."""
    return OptimizerFactory.create(
        strategy_type=strategy_type,
        params=params,
        constraints=constraints,
    )


__all__ = ["OptimizerFactory", "get_optimizer"]