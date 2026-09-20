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

import math
from typing import Any, Callable

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


def _check_finite_number(value: Any, name: str) -> float:
    """F11: 合成参数一律要求有限实数——拒绝 bool/字符串/None/NaN/Inf。"""
    if isinstance(value, bool) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"{name} 必须为有限实数，收到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须为有限实数，收到 {value!r}")
    return number


def faa_composite(
    category_scores: dict[str, pd.DataFrame],
    class_weights: dict[str, float],
    eps: float = EPS,
) -> pd.DataFrame:
    """FAA composite score: L = Σₖ wₖ · norm(catₖ).

    只遍历正权重类别（零权重类别不参与合成，其缺失不影响资格）；跨类别取
    有效性 AND——任一启用类别当日无效则合成得分为 NaN，不用 fill_value
    抹掉缺失。正权重类别缺失（启用但为空）属配置错误。F11: 权重必须
    非负有限——负权重会被计入归一化分母却跳过合成项，静默扭曲其余权重。
    """
    keys = list(category_scores.keys())
    if not keys:
        raise ValueError("No non-empty factor categories to score.")
    for key, weight in class_weights.items():
        checked = _check_finite_number(weight, f"class_weights[{key!r}]")
        if checked < 0:
            raise ValueError(f"class_weights[{key!r}] 必须为非负数，收到 {weight!r}")
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
    属配置错误。F11: β 必须为正有限数——β=0 会把 NaN 格变为 1（缺失被
    激活为合格），负 β 颠倒排序；指数非负有限；幂运算后重新应用原资格
    掩码，任何参数取值都不能把缺失变为合格。
    """
    keys = list(category_scores.keys())
    contributing = [k for k in keys if exponents.get(k, 0.0) > 0]
    if not contributing:
        raise ValueError("exponents must contain at least one positive value.")

    beta_value = _check_finite_number(beta, "beta")
    if beta_value <= 0:
        raise ValueError(
            f"beta 必须为正数，收到 {beta!r}：β=0 会把缺失格激活为合格（NaN**0=1），"
            "负 β 会颠倒排序"
        )
    for key, alpha in exponents.items():
        checked = _check_finite_number(alpha, f"exponents[{key!r}]")
        if checked < 0:
            raise ValueError(f"exponents[{key!r}] 必须为非负数，收到 {alpha!r}")

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
    # F11: 幂运算后重新应用原资格掩码——跨启用类别 AND 的 NaN 语义不因
    # 参数取值失效（β>0 时 NaN**β 本应为 NaN，此处对意外路径兜底）。
    return (product ** beta_value).where(product.notna())


def enabled_category_keys(
    category_scores: dict[str, pd.DataFrame],
    params: dict[str, float],
) -> set[str]:
    """合成实际参与的类别键（F19）。

    与 faa_composite / eaa_composite 的启用口径同源：仅正权重（FAA）/
    正指数（EAA）类别参与合成，其余为禁用类别。调用方应以同一集合过滤
    decision_issues——禁用类别的资格缺失进入决策日志会产生"证券被选中
    却记录被排除"的自相矛盾。
    """
    return {key for key in category_scores if params.get(key, 0.0) > 0}


def filter_issues_by_categories(
    issues: pd.DataFrame | None,
    enabled_categories: set[str],
) -> pd.DataFrame | None:
    """只保留启用类别的资格明细行（F19）。

    禁用类别（权重/指数为 0）不参与合成，其缺失不应出现在决策日志的
    exclusions 中。全量原始明细仍可从 build_category_scores_with_details
    的返回值取得。空/None 明细原样返回。
    """
    if issues is None or issues.empty:
        return issues
    return issues[issues["category"].isin(enabled_categories)]