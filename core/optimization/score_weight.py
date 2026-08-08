"""Score-proportional portfolio optimizer.

Selects the Top N securities by score and assigns weights proportional to each
selected security's score (weight_i = score_i / sum(scores of selected)).

Used by the EAA strategy, where the composite score S is always positive.
"""

from __future__ import annotations

import pandas as pd

from core.optimization.base import PortfolioOptimizer
from core.optimization.exceptions import OptimizationError


class ScoreWeightedOptimizer(PortfolioOptimizer):
    """Select Top N by score and weight proportionally to the score.

    NaN scores are ineligible. Ties are broken by security code for
    deterministic results. Weights are normalized so they sum to 1.
    """

    def __init__(
        self,
        top_n: int = 5,
        max_weight: float = 1.0,
        min_weight: float = 0.0,
    ) -> None:
        _validate_parameters(top_n=top_n, max_weight=max_weight, min_weight=min_weight)
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
        """Return target weights proportional to the selected scores.

        Args:
            factor_scores: Positive score per security code.
            top_n: Number of securities to hold.
            max_weight: Maximum weight per security.
            min_weight: Minimum weight per security.
        """
        resolved_top_n = self.top_n if top_n is None else top_n
        resolved_max_weight = self.max_weight if max_weight is None else max_weight
        resolved_min_weight = self.min_weight if min_weight is None else min_weight
        _validate_parameters(
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

        eligible = scores.dropna()
        eligible = eligible[eligible > 0]  # EAA scores are positive; guard anyway
        if len(eligible) < resolved_top_n:
            raise OptimizationError(
                f"Not enough eligible securities for top_n={resolved_top_n}: "
                f"eligible={len(eligible)}"
            )

        # Sort by code first, then by score (stable) so equal scores break
        # deterministically by security code (ascending).
        ranked = eligible.sort_index(kind="stable").sort_values(
            ascending=False, kind="stable"
        )
        selected = ranked.head(resolved_top_n)

        total = selected.sum()
        weights = selected / total

        if resolved_max_weight < 1.0 and float(weights.max()) > resolved_max_weight:
            raise OptimizationError(
                f"score-proportional weight {weights.max():.4f} exceeds "
                f"max_weight={resolved_max_weight:.4f}."
            )
        if resolved_min_weight > 0.0 and float(weights.min()) < resolved_min_weight:
            raise OptimizationError(
                f"score-proportional weight {weights.min():.4f} is below "
                f"min_weight={resolved_min_weight:.4f}."
            )

        return weights.reindex(scores.index, fill_value=0.0).rename("weight")


def _validate_parameters(top_n: int, max_weight: float, min_weight: float) -> None:
    if top_n <= 0:
        raise ValueError("top_n must be positive.")
    if not 0 <= min_weight <= max_weight <= 1:
        raise ValueError("weight constraints must satisfy 0 <= min_weight <= max_weight <= 1.")