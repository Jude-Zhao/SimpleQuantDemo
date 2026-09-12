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

    便捷函数：委托 :func:`build_category_scores_with_details` 构建一次（不重复
    构建因子），仅返回得分矩阵、丢弃资格明细。严格资格规则（任一必需因子
    无效则类别得分 NaN）详见 core.synthesis.eligibility。
    """
    # 延迟导入避免循环依赖（eligibility 反向复用本模块的构建/归一化函数）。
    from core.synthesis.eligibility import build_category_scores_with_details

    result = build_category_scores_with_details(price_data, universe, categories, resolver=resolver)
    return result.scores


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
    """FAA composite score: L = Σₖ wₖ · norm(catₖ).

    只遍历正权重类别（零权重类别不参与合成，其缺失不影响资格）；跨类别取
    有效性 AND——任一启用类别当日无效则合成得分为 NaN，不用 fill_value
    抹掉缺失。正权重类别缺失（启用但为空）属配置错误。
    """
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
        if wi <= 0:
            continue
        normed = normalize_cross_section(category_scores[key], eps=eps)
        term = normed * wi
        composite = term if composite is None else composite + term
    if composite is None:
        raise ValueError("class_weights must contain at least one positive weight.")

    # 启用（正权重）但被配置为空/缺失的类别 → 配置错误，显式拒绝。
    missing_enabled = {k for k in class_weights if class_weights[k] > 0 and k not in keys}
    if missing_enabled:
        raise ValueError(
            f"启用的类别在配置中为空或缺失: {sorted(missing_enabled)}；"
            "正权重类别必须包含至少一个因子实例"
        )
    return composite


def eaa_composite(
    category_scores: dict[str, pd.DataFrame],
    exponents: dict[str, float],
    beta: float = 1.0,
    eps: float = EPS,
) -> pd.DataFrame:
    """EAA composite score: S = ( Πₖ norm(catₖ)^αₖ )^β.

    只遍历正指数类别；跨启用类别取有效性 AND——任一启用类别当日无效则
    合成得分为 NaN，不用 fill_value 抹掉缺失。正指数类别缺失（启用但为空）
    属配置错误。
    """
    keys = list(category_scores.keys())
    contributing = [k for k in keys if exponents.get(k, 0.0) > 0]
    if not contributing:
        raise ValueError("exponents must contain at least one positive value.")

    # 启用（正指数）但被配置为空/缺失的类别 → 配置错误，显式拒绝。
    missing_enabled = {k for k in exponents if exponents[k] > 0 and k not in keys}
    if missing_enabled:
        raise ValueError(
            f"启用的类别在配置中为空或缺失: {sorted(missing_enabled)}；"
            "正指数类别必须包含至少一个因子实例"
        )

    product = None
    for key in contributing:
        alpha = exponents[key]
        normed = normalize_cross_section(category_scores[key], eps=eps)
        term = normed ** alpha
        product = term if product is None else product * term

    if product is None:
        raise ValueError("No category scores available for EAA composite.")
    return product ** beta