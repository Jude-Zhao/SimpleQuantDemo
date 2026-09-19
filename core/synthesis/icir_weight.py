"""ICIR half-life weighted factor synthesis."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.synthesis.base import FactorSynthesizer
from core.synthesis.exceptions import SynthesisError


class ICIRWeightedSynthesizer(FactorSynthesizer):
    """Combine factors using decayed historical ICIR scores."""

    def __init__(
        self,
        half_life_periods: int = 20,
        min_abs_weight_sum: float = 1e-12,
    ) -> None:
        if half_life_periods <= 0:
            raise ValueError("half_life_periods must be positive.")
        if min_abs_weight_sum <= 0:
            raise ValueError("min_abs_weight_sum must be positive.")
        self.half_life_periods = half_life_periods
        self.min_abs_weight_sum = min_abs_weight_sum

    def synthesize(
        self,
        factor_panel: dict[str, pd.DataFrame],
        icir_data: dict[str, pd.Series],
        half_life_periods: int | None = None,
    ) -> pd.DataFrame:
        """Synthesize factor scores.

        For each factor date, only ICIR observations with dates <= factor date
        are used. The ``<=T`` cut is only free of future leakage when
        ``icir_data`` is indexed by availability dates: forward-return-derived
        statistics (IC, RankIC, ICIR) embed ``close[T+1+h]/close[T+1]-1`` and
        are realized h+1 trading days after their label date T. Map
        label-indexed series through ``core.analysis.map_to_availability_dates``
        first; raw label-indexed ICIR reintroduces lookahead that the ``<=T``
        cut cannot remove.
        """
        half_life = half_life_periods or self.half_life_periods
        if half_life <= 0:
            raise ValueError("half_life_periods must be positive.")
        _validate_synthesis_inputs(factor_panel, icir_data)

        aligned_panel = _align_factor_panel(factor_panel)
        result = pd.DataFrame(
            index=next(iter(aligned_panel.values())).index,
            columns=next(iter(aligned_panel.values())).columns,
            dtype=float,
        )

        for date in result.index:
            raw_scores = {
                factor_name: calculate_decayed_icir_score(
                    icir_series=icir_data[factor_name],
                    as_of_date=date,
                    half_life_periods=half_life,
                )
                for factor_name in aligned_panel
            }
            weights = _normalize_scores(raw_scores, self.min_abs_weight_sum)
            if not weights:
                continue

            synthesized = None
            for factor_name, weight in weights.items():
                weighted_values = aligned_panel[factor_name].loc[date] * weight
                synthesized = weighted_values if synthesized is None else synthesized + weighted_values
            result.loc[date] = synthesized

        result.index.name = "date"
        return result


def calculate_decayed_icir_score(
    icir_series: pd.Series,
    as_of_date: pd.Timestamp,
    half_life_periods: int = 20,
) -> float:
    """Calculate a decayed average ICIR score using data up to as_of_date."""
    if half_life_periods <= 0:
        raise ValueError("half_life_periods must be positive.")
    if not isinstance(icir_series.index, pd.DatetimeIndex):
        raise SynthesisError("ICIR series index must be a DatetimeIndex.")

    as_of = pd.Timestamp(as_of_date).normalize()
    history = icir_series.sort_index().loc[lambda series: series.index <= as_of].dropna()
    if history.empty:
        return float("nan")

    values = history.astype(float).to_numpy()[::-1]
    periods = np.arange(len(values), dtype=float)
    weights = 0.5 ** (periods / half_life_periods)
    return float(np.sum(values * weights) / np.sum(weights))


def _validate_synthesis_inputs(
    factor_panel: dict[str, pd.DataFrame],
    icir_data: dict[str, pd.Series],
) -> None:
    if not factor_panel:
        raise SynthesisError("Factor panel is empty.")
    missing_icir = [factor_name for factor_name in factor_panel if factor_name not in icir_data]
    if missing_icir:
        raise SynthesisError(f"Missing ICIR series for factors: {missing_icir}")


def _align_factor_panel(factor_panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    common_index: pd.DatetimeIndex | None = None
    common_columns: pd.Index | None = None

    for factor_name, factor in factor_panel.items():
        if factor.empty:
            raise SynthesisError(f"Factor {factor_name} is empty.")
        if not isinstance(factor.index, pd.DatetimeIndex):
            raise SynthesisError(f"Factor {factor_name} index must be a DatetimeIndex.")
        common_index = factor.index if common_index is None else common_index.intersection(factor.index)
        common_columns = factor.columns if common_columns is None else common_columns.intersection(factor.columns)

    if common_index is None or common_index.empty:
        raise SynthesisError("Factors have no overlapping dates.")
    if common_columns is None or common_columns.empty:
        raise SynthesisError("Factors have no overlapping securities.")

    common_index = common_index.sort_values()
    return {
        factor_name: factor.loc[common_index, common_columns]
        for factor_name, factor in factor_panel.items()
    }


def _normalize_scores(
    scores: dict[str, float],
    min_abs_weight_sum: float,
) -> dict[str, float]:
    valid_scores = {
        factor_name: score
        for factor_name, score in scores.items()
        if not pd.isna(score) and np.isfinite(score)
    }
    if not valid_scores:
        return {}

    denominator = sum(abs(score) for score in valid_scores.values())
    if denominator < min_abs_weight_sum:
        return {}
    return {
        factor_name: score / denominator
        for factor_name, score in valid_scores.items()
    }

