"""Top-N equal-weight optimizer with optional category constraints."""

from __future__ import annotations

import pandas as pd

from core.optimization.base import PortfolioOptimizer
from core.optimization.constraints import OptimizationConstraints
from core.optimization.exceptions import OptimizationError

_DEFAULT_CATEGORY_KEY = "category"


class EqualWeightOptimizer(PortfolioOptimizer):
    """Select Top N securities by score and assign equal weights.

    When ``constraints`` and ``classifications`` are provided, the selection
    honours per-category minimum/maximum counts (``min_count``/``max_count``).
    Category weight bounds are not enforced at selection time; callers should
    validate the resulting portfolio with ``validate_constraints``.
    """

    def __init__(
        self,
        top_n: int = 5,
        max_weight: float = 0.5,
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
        constraints: OptimizationConstraints | None = None,
        classifications: dict[str, dict[str, str]] | None = None,
    ) -> pd.Series:
        """Return full-universe target weights.

        Securities with NaN scores are not eligible. Ties are resolved by score
        order first and then by security code for deterministic results.

        Args:
            factor_scores: Score per security code.
            top_n: Number of securities to hold.
            max_weight: Maximum weight per security.
            min_weight: Minimum weight per security.
            constraints: Optional category count constraints.
            classifications: ``{sec_code: {category_key: category_value}}``
                used to resolve category membership for count constraints.
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
        ranked = score_table.sort_values(["score", "sec"], ascending=[False, True])

        selected = self._select_securities(
            ranked=ranked,
            top_n=resolved_top_n,
            constraints=constraints,
            classifications=classifications or {},
        )

        weights = pd.Series(0.0, index=scores.index, name="weight")
        weights.loc[selected] = equal_weight
        return weights

    @staticmethod
    def _select_securities(
        ranked: pd.DataFrame,
        top_n: int,
        constraints: OptimizationConstraints | None,
        classifications: dict[str, dict[str, str]],
    ) -> list[str]:
        """Greedy selection honouring per-category min/max counts.

        Returns the list of selected security codes (best score first).
        """
        # Collect count constraints for the default category key.
        count_constraints = []
        if constraints is not None:
            for cat in constraints.category_constraints:
                if cat.category_key != _DEFAULT_CATEGORY_KEY:
                    continue
                if cat.min_count is not None or cat.max_count is not None:
                    count_constraints.append(cat)

        if not count_constraints:
            return ranked.head(top_n)["sec"].tolist()

        def category_of(sec: str) -> str | None:
            return classifications.get(sec, {}).get(_DEFAULT_CATEGORY_KEY)

        # Pre-compute per-category ranked order for min-count feasibility.
        categories: dict[str | None, list[str]] = {}
        for sec in ranked["sec"]:
            categories.setdefault(category_of(sec), []).append(sec)

        # Securities that must be included to satisfy minimum counts.
        must_include: set[str] = set()
        for cat in count_constraints:
            if cat.min_count is None:
                continue
            members = categories.get(cat.category_value, [])
            if len(members) < cat.min_count:
                raise OptimizationError(
                    f"Category '{cat.category_value}' has only {len(members)} eligible "
                    f"securities, but min_count={cat.min_count} is required."
                )
            must_include.update(members[: cat.min_count])

        max_counts = {
            cat.category_value: cat.max_count
            for cat in count_constraints
            if cat.max_count is not None
        }
        selected: list[str] = []
        counts: dict[str, int] = {}

        def _can_add(sec: str) -> bool:
            cat = category_of(sec)
            if cat in max_counts and counts.get(cat or "", 0) >= max_counts[cat]:
                return False
            return True

        # 1. Take all must-include securities (in best-score order).
        for sec in ranked["sec"]:
            if len(selected) >= top_n:
                break
            if sec in must_include and _can_add(sec):
                selected.append(sec)
                cat = category_of(sec)
                counts[cat or ""] = counts.get(cat or "", 0) + 1

        # 2. Fill the rest with the next best securities.
        for sec in ranked["sec"]:
            if len(selected) >= top_n:
                break
            if sec in selected:
                continue
            if not _can_add(sec):
                continue
            selected.append(sec)
            cat = category_of(sec)
            counts[cat or ""] = counts.get(cat or "", 0) + 1

        if len(selected) < top_n:
            raise OptimizationError(
                "Category count constraints prevent selecting "
                f"top_n={top_n} securities (only {len(selected)} available)."
            )
        return selected


def _validate_parameters(top_n: int, max_weight: float, min_weight: float) -> None:
    if top_n <= 0:
        raise ValueError("top_n must be positive.")
    if not 0 <= min_weight <= max_weight <= 1:
        raise ValueError("weight constraints must satisfy 0 <= min_weight <= max_weight <= 1.")
    if top_n * max_weight < 1.0:
        raise OptimizationError(
            f"top_n={top_n} and max_weight={max_weight:.6f} cannot form a fully invested portfolio."
        )