"""Top-N equal-weight optimizer with optional category constraints."""

from __future__ import annotations

import pandas as pd

from core.optimization.base import PortfolioOptimizer
from core.optimization.constraints import OptimizationConstraints
from core.optimization.exceptions import OptimizationError

_DEFAULT_CATEGORY_KEY = "category"


def _merge_count_constraints(
    constraints: OptimizationConstraints | None,
) -> dict[str, tuple[int | None, int | None]]:
    """Merge count constraints per category value, keeping the strictest bound.

    Duplicate constraints for the same category are combined instead of
    overriding each other: ``max_count`` keeps the minimum and ``min_count``
    keeps the maximum. Count constraints on category keys other than
    ``_DEFAULT_CATEGORY_KEY`` are rejected instead of silently ignored.

    Returns:
        Mapping of category value to ``(min_count, max_count)``; either entry
        is ``None`` when that bound is unrestricted.
    """
    merged: dict[str, list[int | None]] = {}
    if constraints is None:
        return {}
    for cat in constraints.category_constraints:
        if cat.category_key != _DEFAULT_CATEGORY_KEY:
            if cat.min_count is not None or cat.max_count is not None:
                raise OptimizationError(
                    f"Category count constraints on category_key='{cat.category_key}' "
                    "are not supported; only category_key="
                    f"'{_DEFAULT_CATEGORY_KEY}' is supported."
                )
            continue
        if cat.min_count is None and cat.max_count is None:
            continue
        if (cat.min_count is not None and cat.min_count < 0) or (
            cat.max_count is not None and cat.max_count < 0
        ):
            raise OptimizationError(
                f"Category '{cat.category_value}' count constraints must be "
                f"non-negative: min_count={cat.min_count}, max_count={cat.max_count}."
            )
        slot = merged.setdefault(cat.category_value, [None, None])
        if cat.min_count is not None and (slot[0] is None or cat.min_count > slot[0]):
            slot[0] = cat.min_count
        if cat.max_count is not None and (slot[1] is None or cat.max_count < slot[1]):
            slot[1] = cat.max_count
    return {value: (bounds[0], bounds[1]) for value, bounds in merged.items()}


class EqualWeightOptimizer(PortfolioOptimizer):
    """Select Top N securities by score and assign equal weights.

    When ``constraints`` and ``classifications`` are provided, the selection
    honours per-category minimum/maximum counts (``min_count``/``max_count``).
    Contradictory, infeasible, or unsupported count constraints raise
    ``OptimizationError`` instead of being partially satisfied. Category
    weight bounds are not enforced at selection time; callers should
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

        Contradictory, infeasible, or unsupported count constraints — and any
        violation of the received count limits in the final selection — raise
        ``OptimizationError`` instead of being silently skipped. The
        must-include set is never truncated to fit ``top_n``.
        """
        merged = _merge_count_constraints(constraints)
        if not merged:
            return ranked.head(top_n)["sec"].tolist()

        def category_of(sec: str) -> str | None:
            return classifications.get(sec, {}).get(_DEFAULT_CATEGORY_KEY)

        # Pre-compute per-category ranked order for min-count feasibility.
        categories: dict[str | None, list[str]] = {}
        for sec in ranked["sec"]:
            categories.setdefault(category_of(sec), []).append(sec)

        # Pre-check: contradictory bounds within one category.
        for value, (min_count, max_count) in merged.items():
            if min_count is not None and max_count is not None and min_count > max_count:
                raise OptimizationError(
                    f"Category '{value}' count constraints are infeasible: "
                    f"min_count={min_count} > max_count={max_count}; the "
                    "must-include securities would violate max_count."
                )

        # Securities that must be included to satisfy minimum counts.
        must_include: set[str] = set()
        total_min_count = 0
        for value, (min_count, _max_count) in merged.items():
            if min_count is None:
                continue
            members = categories.get(value, [])
            if len(members) < min_count:
                raise OptimizationError(
                    f"Category '{value}' has only {len(members)} eligible "
                    f"securities, but min_count={min_count} is required."
                )
            must_include.update(members[:min_count])
            total_min_count += min_count

        # Pre-check: the required minimum counts must fit into the portfolio.
        if total_min_count > top_n:
            raise OptimizationError(
                "Category min_count constraints require at least "
                f"{total_min_count} securities in total, which exceeds "
                f"top_n={top_n}."
            )
        if len(must_include) > top_n:
            raise OptimizationError(
                f"Must-include securities ({len(must_include)}) exceed "
                f"top_n={top_n}."
            )

        # Pre-check: must-include securities must respect their category max.
        for value, (_min_count, max_count) in merged.items():
            if max_count is None:
                continue
            must_in_category = sum(1 for sec in must_include if category_of(sec) == value)
            if must_in_category > max_count:
                raise OptimizationError(
                    f"Must-include securities for category '{value}' "
                    f"({must_in_category}) would violate max_count={max_count}."
                )

        max_counts = {
            value: max_count
            for value, (_min_count, max_count) in merged.items()
            if max_count is not None
        }
        selected: list[str] = []
        counts: dict[str, int] = {}

        def _can_add(sec: str) -> bool:
            cat = category_of(sec)
            if cat in max_counts and counts.get(cat or "", 0) >= max_counts[cat]:
                return False
            return True

        def _add(sec: str) -> None:
            selected.append(sec)
            cat = category_of(sec)
            counts[cat or ""] = counts.get(cat or "", 0) + 1

        # 1. Take all must-include securities (in best-score order). The set
        # is never truncated; if it cannot fit, that is an error, not a skip.
        for sec in ranked["sec"]:
            if sec not in must_include or sec in selected:
                continue
            if len(selected) >= top_n:
                raise OptimizationError(
                    f"Must-include securities cannot fit into top_n={top_n}."
                )
            if not _can_add(sec):
                raise OptimizationError(
                    f"Must-include security '{sec}' would violate its category "
                    "max_count."
                )
            _add(sec)

        # 2. Fill the rest with the next best securities.
        for sec in ranked["sec"]:
            if len(selected) >= top_n:
                break
            if sec in selected:
                continue
            if not _can_add(sec):
                continue
            _add(sec)

        if len(selected) < top_n:
            raise OptimizationError(
                "Category count constraints prevent selecting "
                f"top_n={top_n} securities (only {len(selected)} available)."
            )

        # Post-check: re-verify every received (merged) count constraint
        # against the final selection.
        for value, (min_count, max_count) in merged.items():
            count = sum(1 for sec in selected if category_of(sec) == value)
            if min_count is not None and count < min_count:
                raise OptimizationError(
                    "Selected portfolio violates min_count for category "
                    f"'{value}': count={count} < min_count={min_count}."
                )
            if max_count is not None and count > max_count:
                raise OptimizationError(
                    "Selected portfolio violates max_count for category "
                    f"'{value}': count={count} > max_count={max_count}."
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