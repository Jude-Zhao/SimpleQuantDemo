"""Convenience entrypoint for evaluating a single factor.

Designed so that a researcher (or another agent) only has to implement a
``FactorBuilder`` subclass — the ``build`` logic producing a date×sec score
matrix — and then call :func:`evaluate_factor` once to get IC-based statistics
and an optional close-to-close rotation backtest, without hand-wiring the
factor-analysis → backtest pipeline every time.

This lives in the core layer (pure pandas, no webapp/ORM dependency), so it is
shareable by ``research`` and the webapp. Data is loaded via ``SqliteDataSource``
when ``price_data`` is not supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from core.analysis import (
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
    calculate_rank_ic,
)
from core.backtest import BacktestConfig, calculate_metrics, run_backtest
from core.data import DataSource, SqliteDataSource
from core.factors.base import FactorBuilder
from core.synthesis.faa_eaa import normalize_cross_section

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "simple_quant.db"


@dataclass
class BacktestSummary:
    """Lightweight backtest summary (metrics from the unified framework)."""

    metrics: dict[str, float]
    equity_curve: pd.Series
    n_rebalances: int
    turnover_sum: float


@dataclass
class FactorEvaluation:
    """Result of evaluating a single factor.

    IC/ICIR statistics are always populated. ``backtest`` is ``None`` unless
    ``evaluate_factor(..., backtest=True)``.
    """

    name: str
    factor: pd.DataFrame
    horizon: int
    ic: pd.Series
    rank_ic: pd.Series
    icir: pd.Series
    ic_mean: float
    rank_ic_mean: float
    icir_mean: float
    ic_std: float
    n_observations: int
    warnings: list[str] = field(default_factory=list)
    backtest: BacktestSummary | None = None


def evaluate_factor(
    builder: FactorBuilder,
    *,
    price_data: pd.DataFrame | None = None,
    macro_data: pd.DataFrame | None = None,
    universe: Sequence[str] | None = None,
    data_source: DataSource | None = None,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    horizon: int = 5,
    min_observations: int = 3,
    icir_window: int = 20,
    icir_min_periods: int | None = None,
    backtest: bool = False,
    backtest_freq: str = "5d",
    backtest_top_n: int = 5,
    backtest_weight_mode: str = "equal",
    backtest_max_weight: float = 1.0,
    backtest_min_weight: float = 0.0,
    transaction_cost_bps: float = 0.5,
) -> FactorEvaluation:
    """Evaluate a single factor and return IC / RankIC / ICIR (+ optional backtest).

    Args:
        builder: An instantiated ``FactorBuilder`` (e.g. ``MyFactor(window=20)``).
            Only its ``build`` logic needs to be implemented by the caller.
        price_data: Optional OHLCV long table (``date/sec/open/.../close/...``).
            When omitted, ``data_source`` (or a default ``SqliteDataSource`` on
            ``data/simple_quant.db``) is used to load ``start_date``–``end_date``.
        macro_data: Optional macro data. Defaults to ``pd.DataFrame()`` when the
            caller supplies ``price_data`` directly.
        universe: Optional security-code list. When omitted, inferred from
            ``price_data`` (or the data source).
        data_source: Optional source used when ``price_data`` is not given.
        start_date / end_date: Load range used only when loading from ``data_source``.
        horizon: Forward-return horizon (trading days).
        min_observations: Min cross-sectional observations for one IC point.
        icir_window / icir_min_periods: Rolling ICIR window.
        backtest: When True, also run a close-to-close rotation backtest.
        backtest_freq: "weekly" / "monthly" / "5d".
        backtest_top_n: Number of holdings for the backtest.
        backtest_weight_mode: "equal" (Top-N equal weight) or "score".
        backtest_max_weight / backtest_min_weight: Per-security weight bounds.
        transaction_cost_bps: 统一框架费率（基点），默认 0.5 = 万分之0.5。
    """
    if price_data is None:
        ds = data_source or SqliteDataSource(_DEFAULT_DB)
        loaded_price, loaded_macro, loaded_universe = ds.load_all(
            start_date=start_date,
            end_date=end_date,
        )
        price_data = loaded_price
        if macro_data is None:
            macro_data = loaded_macro
        if universe is None:
            universe = loaded_universe

    if universe is None:
        universe = _infer_universe(price_data)
    universe_list = [str(s).strip().upper() for s in universe]

    factor = builder.build(
        price_data,
        macro_data if macro_data is not None else pd.DataFrame(),
        universe_list,
    )
    forward_returns = calculate_forward_returns(
        price_data, horizon=horizon, universe=universe_list
    )

    ic = calculate_factor_ic(factor, forward_returns, min_observations=min_observations)
    rank_ic = calculate_rank_ic(factor, forward_returns, min_observations=min_observations)
    icir = calculate_icir(ic, window=icir_window, min_periods=icir_min_periods)

    valid_ic = ic.dropna()
    valid_rank = rank_ic.dropna()
    valid_icir = icir.dropna()
    warnings: list[str] = []
    if valid_ic.empty:
        warnings.append(
            "IC series is empty — check the factor matrix (all-NaN rows) or "
            "raise ``min_observations`` / increase universe size."
        )

    backtest_summary: BacktestSummary | None = None
    if backtest:
        # Score-proportional weighting needs strictly-positive scores, but a raw
        # factor can be negative (e.g. a MACD histogram). Normalize the matrix
        # per cross-section to (eps, 1] so the score optimizer always has valid
        # candidates — this matches the EAA semantics used by the real pipeline.
        scores_for_backtest = (
            normalize_cross_section(factor)
            if backtest_weight_mode == "score"
            else factor
        )
        result = run_backtest(
            price_data=price_data,
            factor_scores=scores_for_backtest,
            config=BacktestConfig(
                rebalance_freq=backtest_freq,
                top_n=backtest_top_n,
                max_weight=backtest_max_weight,
                min_weight=backtest_min_weight,
                weight_mode=backtest_weight_mode,
                transaction_cost_bps=transaction_cost_bps,
            ),
        )
        metrics = calculate_metrics(result)
        backtest_summary = BacktestSummary(
            metrics=metrics,
            equity_curve=result.equity_curve,
            n_rebalances=int(metrics["rebalance_count"]),
            turnover_sum=float(metrics["turnover_sum"]),
        )

    return FactorEvaluation(
        name=builder.name,
        factor=factor,
        horizon=horizon,
        ic=ic,
        rank_ic=rank_ic,
        icir=icir,
        ic_mean=float(valid_ic.mean()) if not valid_ic.empty else float("nan"),
        rank_ic_mean=float(valid_rank.mean()) if not valid_rank.empty else float("nan"),
        icir_mean=float(valid_icir.mean()) if not valid_icir.empty else float("nan"),
        ic_std=float(valid_ic.std()) if len(valid_ic) > 1 else float("nan"),
        n_observations=int(len(valid_ic)),
        warnings=warnings,
        backtest=backtest_summary,
    )


def _infer_universe(price_data: pd.DataFrame, field: str = "sec") -> list[str]:
    """Derive the security list from a long-format price table."""
    if field in price_data.columns:
        return sorted(price_data[field].dropna().unique())
    raise ValueError(
        "Cannot infer universe: price_data has no '%s' column and 'universe' "
        "was not provided." % field
    )