"""Collinearity analysis for factor panels."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Literal

import pandas as pd

from core.analysis.exceptions import AnalysisError


@dataclass(frozen=True)
class CorrelatedFactorPair:
    """One highly correlated factor pair."""

    factor_a: str
    factor_b: str
    correlation: float

    def to_warning(self) -> str:
        """Return a human-readable warning line."""
        return f"{self.factor_a} 与 {self.factor_b}: 相关系数 = {self.correlation:.4f}"


@dataclass(frozen=True)
class CollinearityResult:
    """Result of factor collinearity analysis."""

    selected_factor_panel: dict[str, pd.DataFrame]
    dropped_factors: list[str]
    correlated_pairs: list[CorrelatedFactorPair]
    correlation_matrix: pd.DataFrame
    warnings: list[str]


def calculate_factor_correlation_matrix(
    factor_panel: dict[str, pd.DataFrame],
    method: Literal["pearson", "spearman"] = "pearson",
    min_periods: int = 3,
) -> pd.DataFrame:
    """Calculate factor-to-factor correlation from flattened date/security values."""
    if not factor_panel:
        raise AnalysisError("Factor panel is empty.")
    if min_periods <= 1:
        raise ValueError("min_periods must be greater than 1.")
    if method not in {"pearson", "spearman"}:
        raise ValueError("method must be 'pearson' or 'spearman'.")

    flattened = {name: _stack_factor_values(factor) for name, factor in factor_panel.items()}
    factor_values = pd.DataFrame(flattened)
    return factor_values.corr(method=method, min_periods=min_periods)


def find_correlated_pairs(
    correlation_matrix: pd.DataFrame,
    threshold: float = 0.7,
) -> list[CorrelatedFactorPair]:
    """Find factor pairs whose absolute correlation is greater than threshold."""
    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1.")
    if correlation_matrix.empty:
        return []

    pairs: list[CorrelatedFactorPair] = []
    for factor_a, factor_b in combinations(correlation_matrix.columns, 2):
        correlation = correlation_matrix.loc[factor_a, factor_b]
        if pd.isna(correlation):
            continue
        if abs(float(correlation)) > threshold:
            pairs.append(
                CorrelatedFactorPair(
                    factor_a=str(factor_a),
                    factor_b=str(factor_b),
                    correlation=float(correlation),
                )
            )
    return pairs


def analyze_collinearity(
    factor_panel: dict[str, pd.DataFrame],
    icir_data: dict[str, pd.Series] | None = None,
    threshold: float = 0.7,
    mode: Literal["select", "warn"] = "select",
    method: Literal["pearson", "spearman"] = "pearson",
    min_periods: int = 3,
) -> CollinearityResult:
    """Analyze factor collinearity and optionally drop redundant factors.

    mode="select" is intended for research. Highly correlated factors are
    grouped and the factor with the highest latest available ICIR is retained.
    mode="warn" is intended for trading. It keeps all factors and only returns
    warnings.
    """
    if mode not in {"select", "warn"}:
        raise ValueError("mode must be 'select' or 'warn'.")

    correlation_matrix = calculate_factor_correlation_matrix(
        factor_panel=factor_panel,
        method=method,
        min_periods=min_periods,
    )
    correlated_pairs = find_correlated_pairs(correlation_matrix, threshold=threshold)
    warnings = [pair.to_warning() for pair in correlated_pairs]

    if mode == "warn" or not correlated_pairs:
        return CollinearityResult(
            selected_factor_panel=dict(factor_panel),
            dropped_factors=[],
            correlated_pairs=correlated_pairs,
            correlation_matrix=correlation_matrix,
            warnings=warnings,
        )

    if icir_data is None:
        raise AnalysisError("icir_data is required when mode='select'.")

    groups = _build_correlated_groups(list(factor_panel), correlated_pairs)
    keep_factors: set[str] = set(factor_panel)
    dropped_factors: list[str] = []

    for group in groups:
        winner = _select_best_factor(group, icir_data)
        for factor_name in sorted(group):
            if factor_name == winner:
                continue
            keep_factors.discard(factor_name)
            dropped_factors.append(factor_name)

    selected = {
        factor_name: factor
        for factor_name, factor in factor_panel.items()
        if factor_name in keep_factors
    }

    return CollinearityResult(
        selected_factor_panel=selected,
        dropped_factors=sorted(set(dropped_factors)),
        correlated_pairs=correlated_pairs,
        correlation_matrix=correlation_matrix,
        warnings=warnings,
    )


def _build_correlated_groups(
    factor_names: list[str],
    correlated_pairs: list[CorrelatedFactorPair],
) -> list[set[str]]:
    parent = {factor_name: factor_name for factor_name in factor_names}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for pair in correlated_pairs:
        union(pair.factor_a, pair.factor_b)

    groups_by_root: dict[str, set[str]] = {}
    for factor_name in factor_names:
        groups_by_root.setdefault(find(factor_name), set()).add(factor_name)
    return [group for group in groups_by_root.values() if len(group) > 1]


def _stack_factor_values(factor: pd.DataFrame) -> pd.Series:
    try:
        return factor.stack(future_stack=True)
    except (TypeError, ValueError):
        return factor.stack(dropna=False)


def _select_best_factor(group: set[str], icir_data: dict[str, pd.Series]) -> str:
    scores = {
        factor_name: _latest_icir_score(icir_data.get(factor_name))
        for factor_name in group
    }
    return sorted(scores, key=lambda factor_name: (-scores[factor_name], factor_name))[0]


def _latest_icir_score(icir_series: pd.Series | None) -> float:
    if icir_series is None:
        return float("-inf")
    valid_values = icir_series.dropna()
    if valid_values.empty:
        return float("-inf")
    return float(valid_values.iloc[-1])
