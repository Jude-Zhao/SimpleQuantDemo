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
from core.factors.registry import get_factor_class, get_factor_registry
from webapp.schemas.factor import (
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


def list_factors() -> list[FactorMeta]:
    """Return metadata for all registered factors."""
    registry = get_factor_registry()
    result: list[FactorMeta] = []
    for name, cls in registry.items():
        params = {
            k: FactorParamSchema(**v) for k, v in cls.params_schema.items()
        }
        result.append(FactorMeta(
            name=name,
            display_name=getattr(cls, "display_name", name),
            category=getattr(cls, "category", ""),
            description=getattr(cls, "description", ""),
            formula=getattr(cls, "formula", ""),
            direction=getattr(cls, "direction", "positive"),
            params_schema=params,
        ))
    return result


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
    group_returns = _calculate_group_returns(
        factor_matrix, forward_ret, n_groups=n_groups, horizon=horizon
    )

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
) -> FactorCorrelationResponse:
    """Compute the cross-sectional correlation of factor values over time.

    For each date, the factor values across securities are correlated; the
    resulting per-date correlations are averaged into a single matrix.
    """
    panel: dict[str, pd.DataFrame] = {}
    for name in req.factor_names:
        cls = get_factor_class(name)
        if cls is None:
            raise ValueError(f"Factor not found: {name}")
        panel[name] = cls().build(price_data, macro_data, universe)

    if not panel:
        return FactorCorrelationResponse(
            factor_names=[],
            correlation_matrix=[],
        )

    # Align all factor matrices to a common date index.
    common_index = panel[list(panel.keys())[0]].index
    for mat in panel.values():
        common_index = common_index.intersection(mat.index)
    common_index = common_index.sort_values()

    # Collect per-date cross-sectional correlation, then average.
    date_corrs: list[pd.DataFrame] = []
    for date in common_index:
        cross = pd.DataFrame({
            name: mat.loc[date] for name, mat in panel.items()
        }).dropna()
        if len(cross) < 3:
            continue
        corr = cross.corr()
        date_corrs.append(corr)

    if not date_corrs:
        n = len(req.factor_names)
        return FactorCorrelationResponse(
            factor_names=list(req.factor_names),
            correlation_matrix=[
                [1.0 if i == j else 0.0 for j in range(n)]
                for i in range(n)
            ],
        )

    avg_corr = sum(date_corrs) / len(date_corrs)

    names = list(req.factor_names)
    matrix = [
        [float(avg_corr.loc[a, b]) for b in names]
        for a in names
    ]
    return FactorCorrelationResponse(
        factor_names=names,
        correlation_matrix=matrix,
    )


def _calculate_group_returns(
    factor_matrix: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_groups: int = 5,
    horizon: int = 5,
) -> list[FactorGroupReturn]:
    """Calculate average forward return for each factor quintile/decile group."""
    group_returns: dict[int, list[float]] = {i: [] for i in range(1, n_groups + 1)}

    for date in factor_matrix.index:
        if date not in forward_ret.index:
            continue
        fac = factor_matrix.loc[date].dropna()
        ret = forward_ret.loc[date].dropna()
        common = fac.index.intersection(ret.index)
        if len(common) < n_groups:
            continue
        fac = fac[common]
        ret = ret[common]

        # Rank and split into groups
        ranked = fac.rank(pct=True)
        for i in range(n_groups):
            lower = i / n_groups
            upper = (i + 1) / n_groups
            mask = (ranked > lower) & (ranked <= upper)
            if mask.sum() > 0:
                group_returns[i + 1].append(float(ret[mask].mean()))

    result: list[FactorGroupReturn] = []
    for g in range(1, n_groups + 1):
        rets = group_returns[g]
        if not rets:
            result.append(FactorGroupReturn(group=g, annual_return=0.0, cumulative_return=0.0))
            continue
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        cumulative = cum - 1.0
        # Annualize: assume horizon trading days per period, ~252 trading days/year
        n_periods = len(rets)
        if n_periods > 0 and cumulative > -1:
            annual = math.pow(1 + cumulative, 252 / (horizon * n_periods)) - 1
        else:
            annual = 0.0
        result.append(FactorGroupReturn(
            group=g,
            annual_return=float(annual),
            cumulative_return=float(cumulative),
        ))

    return result
