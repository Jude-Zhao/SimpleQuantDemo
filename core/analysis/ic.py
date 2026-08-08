"""Forward-return, IC, RankIC, and ICIR calculations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from core.analysis.exceptions import AnalysisError
from core.factors.utils import pivot_price_field


def calculate_forward_returns(
    price_data: pd.DataFrame,
    horizon: int = 5,
    universe: Sequence[str] | None = None,
    price_field: str = "close",
) -> pd.DataFrame:
    """Calculate close[t+horizon] / close[t] - 1 as a date-by-security matrix."""
    if horizon <= 0:
        raise ValueError("horizon must be positive.")

    close = pivot_price_field(price_data, field=price_field, universe=universe)
    forward_returns = close.shift(-horizon) / close - 1.0
    forward_returns.index.name = "date"
    return forward_returns


def calculate_factor_ic(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_observations: int = 3,
    method: str = "pearson",
) -> pd.Series:
    """Calculate cross-sectional IC between one factor and forward returns by date."""
    if min_observations <= 1:
        raise ValueError("min_observations must be greater than 1.")
    if method not in {"pearson", "spearman"}:
        raise ValueError("method must be 'pearson' or 'spearman'.")

    factor_aligned, returns_aligned = _align_factor_and_returns(factor, forward_returns)
    values: dict[pd.Timestamp, float] = {}

    for date in factor_aligned.index:
        pair = pd.concat(
            [
                factor_aligned.loc[date].rename("factor"),
                returns_aligned.loc[date].rename("forward_return"),
            ],
            axis=1,
        ).dropna()

        if len(pair) < min_observations:
            values[date] = float("nan")
            continue
        values[date] = pair["factor"].corr(pair["forward_return"], method=method)

    result = pd.Series(values, name=f"ic_{method}")
    result.index = pd.DatetimeIndex(result.index, name="date")
    return result


def calculate_rank_ic(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    min_observations: int = 3,
) -> pd.Series:
    """Calculate Spearman RankIC between one factor and forward returns by date."""
    return calculate_factor_ic(
        factor=factor,
        forward_returns=forward_returns,
        min_observations=min_observations,
        method="spearman",
    ).rename("rank_ic")


def calculate_factor_panel_ic(
    factor_panel: Mapping[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    min_observations: int = 3,
    method: str = "pearson",
) -> dict[str, pd.Series]:
    """Calculate IC series for each factor in a factor panel."""
    if not factor_panel:
        raise AnalysisError("Factor panel is empty.")

    return {
        factor_name: calculate_factor_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_observations=min_observations,
            method=method,
        )
        for factor_name, factor in factor_panel.items()
    }


def calculate_icir(
    ic_series: pd.Series,
    window: int = 20,
    min_periods: int | None = None,
) -> pd.Series:
    """Calculate rolling ICIR = rolling_mean(IC) / rolling_std(IC)."""
    if window <= 1:
        raise ValueError("window must be greater than 1.")
    if min_periods is None:
        min_periods = window
    if min_periods <= 1:
        raise ValueError("min_periods must be greater than 1.")

    ic = ic_series.astype(float).sort_index()
    rolling_mean = ic.rolling(window=window, min_periods=min_periods).mean()
    rolling_std = ic.rolling(window=window, min_periods=min_periods).std()
    icir = rolling_mean / rolling_std
    icir = icir.mask(rolling_std == 0)
    icir.name = "icir"
    return icir


def _align_factor_and_returns(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if factor.empty:
        raise AnalysisError("Factor matrix is empty.")
    if forward_returns.empty:
        raise AnalysisError("Forward return matrix is empty.")
    if not isinstance(factor.index, pd.DatetimeIndex):
        raise AnalysisError("Factor index must be a DatetimeIndex.")
    if not isinstance(forward_returns.index, pd.DatetimeIndex):
        raise AnalysisError("Forward return index must be a DatetimeIndex.")

    common_dates = factor.index.intersection(forward_returns.index).sort_values()
    common_columns = factor.columns.intersection(forward_returns.columns)
    if common_dates.empty:
        raise AnalysisError("Factor and forward return matrices have no overlapping dates.")
    if common_columns.empty:
        raise AnalysisError("Factor and forward return matrices have no overlapping securities.")

    factor_aligned = factor.loc[common_dates, common_columns].sort_index()
    returns_aligned = forward_returns.loc[common_dates, common_columns].sort_index()
    return factor_aligned, returns_aligned

