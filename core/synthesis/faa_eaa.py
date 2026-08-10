"""EAA / FAA factor-category scoring pipeline (pure functions).

Both strategies share the same front-end pipeline:

1. Build each configured factor instance into a date-by-security matrix.
2. Normalize every factor to (eps, 1] per cross-section (min-max).
3. Equal-weight factors within a category  -> category score matrix.
4. Combine category scores into a composite score:
   - FAA: weighted linear sum  L = Σ wₖ · norm(catₖ)
   - EAA: power-product           S = ( Π norm(catₖ)^αₖ )^β
5. Select Top N and produce target weights (equal-weight for FAA,
   score-proportional for EAA).

These functions are factor-source agnostic: factor instances are resolved
through an optional ``resolver`` (defaulting to the core registry), so the
same synthesis logic can be reused by the webapp (core/builtin factors) and
by ``research`` (its own experimental factor pool).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.config import FactorCategory
from core.factors.registry import get_factor_class

# Floor used by min-max normalization to avoid 0 values in EAA's power product
# (0^α would collapse the whole product to 0).
EPS = 0.01

# Resolves a factor registry name to a builder class.
Resolver = Callable[[str], type[FactorBuilder] | None]


def normalize_cross_section(df: pd.DataFrame, eps: float = EPS) -> pd.DataFrame:
    """Normalize each row (cross-section) to (eps, 1] via min-max.

    - A row whose values are all identical (no discrimination) maps to 1.0.
    - A row that is entirely NaN stays NaN.
    - Missing cells within a row stay NaN.
    """
    df = pd.DataFrame(df)
    mn = df.min(axis=1, skipna=True)
    mx = df.max(axis=1, skipna=True)
    span = mx - mn
    tiny = span < 1e-12

    result = df.sub(mn, axis=0).div(span.where(~tiny, 1.0), axis=0)
    result = result * (1 - eps) + eps
    result.loc[df.isna().all(axis=1)] = np.nan
    # No-discrimination rows map valid cells to 1.0 but keep NaN cells as NaN.
    result.loc[tiny] = 1.0
    result[df.isna()] = np.nan
    return result


def build_category_factors(
    price_data: pd.DataFrame,
    universe: list[str],
    category: FactorCategory,
    resolver: Resolver | None = None,
) -> list[pd.DataFrame]:
    """Build every factor instance in a category into a matrix.

    ``resolver`` is a callable(name: str) -> builder class | None. When omitted,
    the core factor registry is used (``get_factor_class``).
    """
    use_resolver = resolver or get_factor_class
    matrices: list[pd.DataFrame] = []
    for inst in category.factors:
        cls = use_resolver(inst.name)
        if cls is None:
            raise ValueError(f"Factor '{inst.name}' is not registered.")
        factor = cls(**inst.params).build(price_data, pd.DataFrame(), universe)
        matrices.append(factor)
    return matrices


def build_category_scores(
    price_data: pd.DataFrame,
    universe: list[str],
    categories: tuple[FactorCategory, ...],
    resolver: Resolver | None = None,
) -> dict[str, pd.DataFrame]:
    """Return {category_key: category_score_matrix} for non-empty categories.

    Category score = equal-weight mean of per-factor normalized matrices.
    """
    result: dict[str, pd.DataFrame] = {}
    for cat in categories:
        if cat.is_empty:
            continue
        matrices = build_category_factors(price_data, universe, cat, resolver=resolver)
        if not matrices:
            continue
        score = category_score_from_matrices(matrices)
        if score is not None:
            result[cat.key] = score
    return result


def category_score_from_matrices(matrices: list[pd.DataFrame]) -> pd.DataFrame | None:
    """Equal-weight mean of a category's per-factor normalized matrices.

    Each factor matrix is min-max normalized per cross-section, then averaged
    across instances ignoring NaN cells per (date, sec). Returns ``None`` when
    ``matrices`` is empty (no factors to synthesize).
    """
    normed = [normalize_cross_section(m) for m in matrices]
    if not normed:
        return None
    arr = np.stack([m.values for m in normed], axis=2).astype(float)
    valid = ~np.isnan(arr)
    count = valid.sum(axis=2)
    with np.errstate(invalid="ignore"):
        mean_vals = np.where(valid, arr, 0.0).sum(axis=2) / np.where(
            count == 0, 1, count
        )
    mean_vals[count == 0] = np.nan
    return pd.DataFrame(mean_vals, index=normed[0].index, columns=normed[0].columns)


def _reindex_weights(
    weights: dict[str, float],
    category_keys: list[str],
) -> np.ndarray:
    """Align a {cat_key: weight} dict to category order, defaulting to 0."""
    return np.array([weights.get(k, 0.0) for k in category_keys], dtype=float)


def faa_composite(
    category_scores: dict[str, pd.DataFrame],
    class_weights: dict[str, float],
    eps: float = EPS,
) -> pd.DataFrame:
    """FAA composite score: L = Σₖ wₖ · norm(catₖ)."""
    keys = list(category_scores.keys())
    if not keys:
        raise ValueError("No non-empty factor categories to score.")
    w = _reindex_weights(class_weights, keys)
    total = w.sum()
    if total <= 0:
        raise ValueError("class_weights must contain at least one positive weight.")
    w = w / total  # normalize to sum=1

    composite = None
    for wi, key in zip(w, keys):
        normed = normalize_cross_section(category_scores[key], eps=eps)
        term = normed * wi
        composite = term if composite is None else composite.add(term, fill_value=0.0)
    return composite


def eaa_composite(
    category_scores: dict[str, pd.DataFrame],
    exponents: dict[str, float],
    beta: float = 1.0,
    eps: float = EPS,
) -> pd.DataFrame:
    """EAA composite score: S = ( Πₖ norm(catₖ)^αₖ )^β.

    Only categories with a positive exponent contribute. A category whose score
    is entirely NaN is skipped for that day.
    """
    keys = list(category_scores.keys())
    contributing = [k for k in keys if exponents.get(k, 0.0) > 0]
    if not contributing:
        raise ValueError("exponents must contain at least one positive value.")

    product = None
    for key in contributing:
        alpha = exponents[key]
        normed = normalize_cross_section(category_scores[key], eps=eps)
        term = normed ** alpha
        product = term if product is None else product.mul(term, fill_value=1.0)

    if product is None:
        raise ValueError("No category scores available for EAA composite.")
    return product ** beta