"""Factor computation and analysis service."""

from __future__ import annotations

import math

import pandas as pd

from core.analysis.ic import (
    calculate_forward_returns,
    calculate_icir,
    calculate_factor_ic,
    calculate_rank_ic,
)
from core.factors.config import FactorCategory, FactorInstance, list_factor_categories
from core.factors.registry import get_factor_class
from core.factors.utils import pivot_price_field
from webapp.schemas.factor import (
    FactorCategoryMeta,
    FactorComputeResponse,
    FactorGroupReturn,
    FactorICResult,
    FactorMeta,
    FactorParamSchema,
)
from webapp.schemas.factor_correlation import (
    FactorCorrelationRequest,
    FactorCorrelationResponse,
)


def instance_id(inst: FactorInstance) -> str:
    """Stable label for a factor instance: ``name(window)`` or ``name``."""
    window = inst.params.get("window")
    return f"{inst.name}({window})" if window is not None else inst.name


def list_factor_categories_meta() -> list[FactorCategoryMeta]:
    """Return factor metadata organized by the ``factors.yaml`` categories."""
    result: list[FactorCategoryMeta] = []
    for cat in list_factor_categories():
        cat_meta = FactorCategoryMeta(
            key=cat.key,
            display_name=cat.display_name,
            is_empty=cat.is_empty,
            factors=[],
        )
        for inst in cat.factors:
            cls = get_factor_class(inst.name)
            if cls is None:
                continue
            params = {
                k: FactorParamSchema(**v) for k, v in cls.params_schema.items()
            }
            cat_meta.factors.append(FactorMeta(
                id=instance_id(inst),
                name=inst.name,
                display_name=getattr(cls, "display_name", inst.name),
                category=cat.display_name,
                description=getattr(cls, "description", ""),
                formula=getattr(cls, "formula", ""),
                direction=getattr(cls, "direction", "positive"),
                params=dict(inst.params),
                params_schema=params,
            ))
        result.append(cat_meta)
    return result


def resolve_instance(instance_id_or_name: str) -> tuple[str, dict]:
    """Resolve a factor id (``momentum(20)``) or plain name into (name, params).

    Plain names resolve to the registry default params (no window override).
    """
    if "(" in instance_id_or_name and instance_id_or_name.endswith(")"):
        name, raw = instance_id_or_name.split("(", 1)
        window = raw[:-1]
        try:
            return name, {"window": int(window)}
        except ValueError:
            return instance_id_or_name, {}
    return instance_id_or_name, {}


def compute_factor(
    factor_name: str,
    params: dict,
    price_data: pd.DataFrame,
    macro_data: pd.DataFrame,
    universe: list[str],
    horizon: int = 5,
    n_groups: int = 5,
) -> FactorComputeResponse:
    """Compute a factor and return IC analysis + group returns."""
    cls = get_factor_class(factor_name)
    if cls is None:
        raise ValueError(f"Factor not found: {factor_name}")

    factor = cls(**params)
    factor_matrix = factor.build(price_data, macro_data, universe)

    # Forward returns
    forward_ret = calculate_forward_returns(price_data, horizon=horizon, universe=universe)

    # IC series
    ic_series = calculate_factor_ic(factor_matrix, forward_ret, method="pearson")
    rank_ic_series = calculate_rank_ic(factor_matrix, forward_ret)
    icir_series = calculate_icir(ic_series, window=20)

    # Summary stats
    ic_mean = float(ic_series.mean()) if not ic_series.dropna().empty else 0.0
    ic_std = float(ic_series.std()) if not ic_series.dropna().empty else 0.0
    icir = ic_mean / ic_std if ic_std != 0 else 0.0

    rank_ic_mean = float(rank_ic_series.mean()) if not rank_ic_series.dropna().empty else 0.0
    rank_ic_std = float(rank_ic_series.std()) if not rank_ic_series.dropna().empty else 0.0
    rank_icir = rank_ic_mean / rank_ic_std if rank_ic_std != 0 else 0.0

    # Convert series to dicts with date strings
    ic_dict = {
        str(k.date()): float(v) for k, v in ic_series.dropna().items()
    }
    rank_ic_dict = {
        str(k.date()): float(v) for k, v in rank_ic_series.dropna().items()
    }
    icir_dict = {
        str(k.date()): float(v) for k, v in icir_series.dropna().items()
    }

    ic_result = FactorICResult(
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        rank_ic_mean=rank_ic_mean,
        rank_ic_std=rank_ic_std,
        rank_icir=rank_icir,
        ic_series=ic_dict,
        rank_ic_series=rank_ic_dict,
        icir_series=icir_dict,
    )

    # Group returns
    close = pivot_price_field(price_data, field="close", universe=universe)
    group_returns = _calculate_group_returns(factor_matrix, close, n_groups=n_groups)

    return FactorComputeResponse(
        factor_name=factor_name,
        display_name=getattr(cls, "display_name", factor_name),
        ic_result=ic_result,
        group_returns=group_returns,
    )


def compute_factor_correlation(
    req: FactorCorrelationRequest,
    price_data: pd.DataFrame,
    macro_data: pd.DataFrame,
    universe: list[str],
    categories: tuple[FactorCategory, ...] | None = None,
) -> FactorCorrelationResponse:
    """Compute a correlation matrix between factors.

    At ``instance`` granularity, each requested factor id resolves to a factor
    matrix built with its own params. At ``class`` granularity, non-empty
    categories are collapsed into equal-weighted category scores and their
    correlation is returned.
    """
    if req.granularity == "class":
        return _class_granularity_correlation(
            price_data, universe, categories
        )
    return _instance_granularity_correlation(
        req, price_data, macro_data, universe
    )


def _correlation_of_panel(
    panel: dict[str, pd.DataFrame],
) -> tuple[list[str], list[list[float]]]:
    """Cross-sectional correlation of factor matrices averaged over dates."""
    if not panel:
        return [], []

    common_index = panel[list(panel.keys())[0]].index
    for mat in panel.values():
        common_index = common_index.intersection(mat.index)
    common_index = common_index.sort_values()

    date_corrs: list[pd.DataFrame] = []
    for date in common_index:
        cross = pd.DataFrame({
            name: mat.loc[date] for name, mat in panel.items()
        }).dropna()
        if len(cross) < 3:
            continue
        corr = cross.corr()
        date_corrs.append(corr)

    names = list(panel.keys())
    if not date_corrs:
        return names, [
            [1.0 if i == j else 0.0 for j in range(len(names))]
            for i in range(len(names))
        ]

    avg_corr = sum(date_corrs) / len(date_corrs)
    matrix = [
        [float(avg_corr.loc[a, b]) for b in names]
        for a in names
    ]
    return names, matrix


def _instance_granularity_correlation(
    req: FactorCorrelationRequest,
    price_data: pd.DataFrame,
    macro_data: pd.DataFrame,
    universe: list[str],
) -> FactorCorrelationResponse:
    panel: dict[str, pd.DataFrame] = {}
    for fid in req.factor_ids:
        name, params = resolve_instance(fid)
        cls = get_factor_class(name)
        if cls is None:
            raise ValueError(f"Factor not found: {name}")
        panel[fid] = cls(**params).build(price_data, macro_data, universe)

    names, matrix = _correlation_of_panel(panel)
    return FactorCorrelationResponse(
        granularity="instance",
        labels=names,
        correlation_matrix=matrix,
    )


def _class_granularity_correlation(
    price_data: pd.DataFrame,
    universe: list[str],
    categories: tuple[FactorCategory, ...] | None,
) -> FactorCorrelationResponse:
    cats = categories if categories is not None else list_factor_categories()
    from webapp.services.eaa_faa import build_category_scores

    scores = build_category_scores(price_data, universe, cats)
    panel = {key: mat for key, mat in scores.items()}
    names, matrix = _correlation_of_panel(panel)
    return FactorCorrelationResponse(
        granularity="class",
        labels=names,
        correlation_matrix=matrix,
    )


def _calculate_group_returns(
    factor_matrix: pd.DataFrame,
    close: pd.DataFrame,
    n_groups: int = 5,
) -> list[FactorGroupReturn]:
    """Daily-rebalanced group portfolio returns.

    每个信号日按因子 pct-rank 分组；T 日信号在 T+1 收盘建仓、T+2 收盘
    调仓（与 IC 的 T+1 进场口径一致），组内等权的日收益逐日复利得到可
    实现的净值路径——重叠持有期不再被重复计数。
    """
    daily_ret = close.pct_change(fill_method=None).reindex(
        index=factor_matrix.index, columns=factor_matrix.columns
    )
    dates = factor_matrix.index

    group_daily: dict[int, list[float]] = {i: [] for i in range(1, n_groups + 1)}
    for k in range(len(dates) - 2):
        fac = factor_matrix.iloc[k].dropna()
        dr = daily_ret.iloc[k + 2].dropna()
        common = fac.index.intersection(dr.index)
        if len(common) < n_groups:
            continue
        fac = fac[common]
        ranked = fac.rank(pct=True)
        for i in range(n_groups):
            lower = i / n_groups
            upper = (i + 1) / n_groups
            mask = (ranked > lower) & (ranked <= upper)
            if mask.sum() > 0:
                group_daily[i + 1].append(float(dr[mask].mean()))

    result: list[FactorGroupReturn] = []
    for g in range(1, n_groups + 1):
        rets = group_daily[g]
        if not rets:
            result.append(FactorGroupReturn(group=g, annual_return=0.0, cumulative_return=0.0))
            continue
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        cumulative = cum - 1.0
        n_days = len(rets)
        if n_days > 0 and cum > 0:
            annual = math.pow(cum, 252 / n_days) - 1
        else:
            annual = 0.0
        result.append(FactorGroupReturn(
            group=g,
            annual_return=float(annual),
            cumulative_return=float(cumulative),
        ))

    return result
