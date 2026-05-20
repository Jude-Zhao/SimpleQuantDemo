"""Trading position signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.analysis import (
    analyze_collinearity,
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
)
from core.calendar import generate_rebalance_dates, get_trading_dates
from core.data import CsvDataSource
from core.factors import MomentumFactor, VolatilityFactor
from core.optimization import EqualWeightOptimizer
from core.synthesis import ICIRWeightedSynthesizer
from trading.config import TradingConfig


@dataclass(frozen=True)
class TradingSignalResult:
    """Trading signal output."""

    signal_date: pd.Timestamp
    positions: pd.DataFrame
    warnings: list[str]
    output_path: Path


def generate_trading_signal(config: TradingConfig) -> TradingSignalResult:
    """Generate latest position signal from local data."""
    data_source = CsvDataSource(
        etf_price_path=config.etf_price_path,
        macro_factors_path=config.macro_factors_path,
        universe_path=config.universe_path,
    )
    price_data, macro_data, universe = data_source.load_all(
        start_date=config.start_date,
        end_date=config.end_date,
    )
    factor_panel = {
        f"momentum_{config.momentum_window}": MomentumFactor(config.momentum_window).build(
            price_data,
            macro_data,
            universe,
        ),
        f"volatility_{config.volatility_window}": VolatilityFactor(config.volatility_window).build(
            price_data,
            macro_data,
            universe,
        ),
    }
    forward_returns = calculate_forward_returns(
        price_data,
        horizon=config.forward_return_horizon,
        universe=universe,
    )
    ic_dates = generate_rebalance_dates(
        trading_dates=get_trading_dates(price_data).intersection(forward_returns.index),
        rebalance_freq=config.rebalance_freq,  # type: ignore[arg-type]
        rebalance_day=config.rebalance_day,
    )
    icir_data = {
        factor_name: calculate_icir(
            calculate_factor_ic(factor, forward_returns, min_periods=config.ic_min_periods).loc[
                lambda series: series.index.intersection(ic_dates)
            ],
            window=config.icir_window,
            min_periods=config.icir_min_periods,
        )
        for factor_name, factor in factor_panel.items()
    }
    collinearity = analyze_collinearity(
        factor_panel=factor_panel,
        threshold=config.collinearity_threshold,
        mode="warn",
        min_periods=config.ic_min_periods,
    )
    synthesized_scores = ICIRWeightedSynthesizer(
        half_life_periods=config.half_life_periods,
    ).synthesize(
        factor_panel=collinearity.selected_factor_panel,
        icir_data=icir_data,
    )
    latest_scores = synthesized_scores.dropna(how="all").iloc[-1]
    signal_date = pd.Timestamp(latest_scores.name).normalize()
    weights = EqualWeightOptimizer(
        top_n=config.top_n,
        max_weight=config.max_weight,
        min_weight=config.min_weight,
    ).optimize(latest_scores)

    positions = (
        weights[weights > 0]
        .rename("weight")
        .reset_index()
        .rename(columns={"index": "sec"})
        .sort_values("weight", ascending=False)
        .reset_index(drop=True)
    )
    output_path = write_positions(config.output_dir, signal_date, positions)
    return TradingSignalResult(
        signal_date=signal_date,
        positions=positions,
        warnings=collinearity.warnings,
        output_path=output_path,
    )


def write_positions(output_dir: Path, signal_date: pd.Timestamp, positions: pd.DataFrame) -> Path:
    """Write position signal CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"position_{signal_date.strftime('%Y%m%d')}.csv"
    positions.to_csv(path, index=False, encoding="utf-8-sig")
    return path
