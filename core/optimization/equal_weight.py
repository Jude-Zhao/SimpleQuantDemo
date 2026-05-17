"""Top-N equal-weight optimizer."""

from __future__ import annotations

import pandas as pd

from core.optimization.base import PortfolioOptimizer
from core.optimization.exceptions import OptimizationError


class EqualWeightOptimizer(PortfolioOptimizer):
    """Select Top N securities by score and assign equal weights."""

    def __init__(
        self,
        top_n: int = 5,
        max_weight: float = 0.5,
        min_weight: float = 0.0,
    ) -> None:
        _validate_constraints(top_n=top_n, max_weight=max_weight, min_weight=min_weight)
        self.top_n = top_n
        self.max_weight = max_weight
        self.min_weight = min_weight

    def optimize(
        self,
        factor_scores: pd.Series,
        top_n: int | None = None,
        max_weight: float | None = None,
        min_weight: float | None = None,
    ) -> pd.Series:
        """Return full-universe target weights.

        Securities with NaN scores are not eligible. Ties are resolved by score
        order first and then by security code for deterministic results.
        """
        resolved_top_n = self.top_n if top_n is None else top_n
        resolved_max_weight = self.max_weight if max_weight is None else max_weight
        resolved_min_weight = self.min_weight if min_weight is None else min_weight
        _validate_constraints(
            top_n=resolved_top_n,
            max_weight=resolved_max_weight,
            min_weight=resolved_min_weight,
        )

        if factor_scores.empty:
            raise OptimizationError("factor_scores is empty.")
        scores = factor_scores.copy()
        scores.index = scores.index.astype(str).str.strip().str.upper()
        if scores.index.has_duplicates:
            duplicates = scores.index[scores.index.duplicated()].unique().tolist()
            raise OptimizationError(f"factor_scores contains duplicate securities: {duplicates}")

        eligible_scores = scores.dropna()
        if len(eligible_scores) < resolved_top_n:
            raise OptimizationError(
                f"Not enough eligible securities for top_n={resolved_top_n}: "
                f"eligible={len(eligible_scores)}"
            )

        equal_weight = 1.0 / resolved_top_n
        if equal_weight > resolved_max_weight:
            raise OptimizationError(
                f"top_n={resolved_top_n} implies weight={equal_weight:.6f}, "
                f"which exceeds max_weight={resolved_max_weight:.6f}."
            )
        if equal_weight < resolved_min_weight:
            raise OptimizationError(
                f"top_n={resolved_top_n} implies weight={equal_weight:.6f}, "
                f"which is below min_weight={resolved_min_weight:.6f}."
            )

        score_table = eligible_scores.rename("score").reset_index()
        score_table.columns = ["sec", "score"]
        selected = (
            score_table.sort_values(["score", "sec"], ascending=[False, True])
            .head(resolved_top_n)["sec"]
            .tolist()
        )

        weights = pd.Series(0.0, index=scores.index, name="weight")
        weights.loc[selected] = equal_weight
        return weights


def _validate_constraints(top_n: int, max_weight: float, min_weight: float) -> None:
    if top_n <= 0:
        raise ValueError("top_n must be positive.")
    if not 0 <= min_weight <= max_weight <= 1:
        raise ValueError("weight constraints must satisfy 0 <= min_weight <= max_weight <= 1.")
    if top_n * max_weight < 1.0:
        raise OptimizationError(
            f"top_n={top_n} and max_weight={max_weight:.6f} cannot form a fully invested portfolio."
        )
