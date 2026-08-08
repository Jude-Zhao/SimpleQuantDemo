"""Research pipeline entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
import quantstats as qs

from core.analysis import (
    analyze_collinearity,
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
    calculate_rank_ic,
)
from core.calendar import generate_rebalance_dates, get_trading_dates
from core.data import SqliteDataSource
from core.factors import MomentumFactor, VolatilityFactor
from core.synthesis import ICIRWeightedSynthesizer
from research.backtest import BacktestResult, run_backtest
from research.config import ResearchConfig, default_research_config
from research.visualization import plot_equity_curve, plot_factor_stats, plot_latest_weights


@dataclass(frozen=True)
class ResearchRunResult:
    """Outputs from one research pipeline run."""

    backtest: BacktestResult
    selected_factors: list[str]
    dropped_factors: list[str]
    warnings: list[str]
    summary: pd.Series
    output_paths: dict[str, Path]


def run_research(config: ResearchConfig) -> ResearchRunResult:
    """Run the built-in local-data research pipeline."""
    data_source = SqliteDataSource(db_path=config.db_path)
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
        rebalance_freq=config.backtest.rebalance_freq,  # type: ignore[arg-type]
        rebalance_day=config.backtest.rebalance_day,
    )

    ic_data = {
        factor_name: calculate_factor_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_observations=config.ic_min_observations,
        ).loc[lambda series: series.index.intersection(ic_dates)]
        for factor_name, factor in factor_panel.items()
    }
    rank_ic_data = {
        factor_name: calculate_rank_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_observations=config.ic_min_observations,
        ).loc[lambda series: series.index.intersection(ic_dates)]
        for factor_name, factor in factor_panel.items()
    }
    icir_data = {
        factor_name: calculate_icir(
            ic_series=ic_series,
            window=config.icir_window,
            min_periods=config.icir_min_periods,
        )
        for factor_name, ic_series in ic_data.items()
    }

    collinearity = analyze_collinearity(
        factor_panel=factor_panel,
        icir_data=icir_data,
        threshold=config.collinearity_threshold,
        mode="select",
        min_observations=config.ic_min_observations,
    )
    selected_icir_data = {
        factor_name: icir_data[factor_name]
        for factor_name in collinearity.selected_factor_panel
    }
    synthesized_scores = ICIRWeightedSynthesizer(
        half_life_periods=config.half_life_periods,
    ).synthesize(
        factor_panel=collinearity.selected_factor_panel,
        icir_data=selected_icir_data,
    )

    backtest_result = run_backtest(
        price_data=price_data,
        factor_scores=synthesized_scores,
        config=config.backtest,
    )
    summary = calculate_backtest_summary(backtest_result)
    output_paths = write_research_outputs(
        output_dir=config.output_dir,
        backtest_result=backtest_result,
        summary=summary,
        ic_data=ic_data,
        rank_ic_data=rank_ic_data,
        icir_data=icir_data,
        synthesized_scores=synthesized_scores,
        warnings=collinearity.warnings,
        dropped_factors=collinearity.dropped_factors,
    )

    return ResearchRunResult(
        backtest=backtest_result,
        selected_factors=list(collinearity.selected_factor_panel),
        dropped_factors=collinearity.dropped_factors,
        warnings=collinearity.warnings,
        summary=summary,
        output_paths=output_paths,
    )


def calculate_backtest_summary(result: BacktestResult, annualization: int = 252) -> pd.Series:
    """Calculate performance metrics with quantstats.

    Metric calculations (annual return, volatility, Sharpe, drawdown,
    Sortino, Calmar, win rate) are delegated to ``quantstats``. The
    turnover / cost / rebalance counts are backtest-specific and kept
    from the engine result.
    """
    returns = result.daily_returns.astype(float)
    equity_curve = result.equity_curve.astype(float)
    total_return = equity_curve.iloc[-1] - 1.0

    return pd.Series(
        {
            "total_return": total_return,
            "annual_return": qs.stats.cagr(returns, periods=annualization),
            "annual_volatility": qs.stats.volatility(returns, periods=annualization),
            "sharpe": qs.stats.sharpe(returns, periods=annualization),
            "max_drawdown": qs.stats.max_drawdown(returns),
            "sortino": qs.stats.sortino(returns, periods=annualization),
            "calmar": qs.stats.calmar(returns, periods=annualization),
            "win_rate": qs.stats.win_rate(returns),
            "turnover_sum": result.turnover.sum(),
            "cost_sum": result.costs.sum(),
            "rebalance_count": len(result.rebalance_dates),
        },
        name="summary",
    )


def write_research_outputs(
    output_dir: Path,
    backtest_result: BacktestResult,
    summary: pd.Series,
    ic_data: dict[str, pd.Series],
    rank_ic_data: dict[str, pd.Series],
    icir_data: dict[str, pd.Series],
    synthesized_scores: pd.DataFrame,
    warnings: list[str],
    dropped_factors: list[str],
) -> dict[str, Path]:
    """Write research outputs to CSV/TXT files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "equity_curve": output_dir / "equity_curve.csv",
        "weights": output_dir / "weights.csv",
        "summary": output_dir / "summary.csv",
        "factor_stats": output_dir / "factor_stats.csv",
        "synthesized_scores": output_dir / "synthesized_scores.csv",
        "warnings": output_dir / "warnings.txt",
        "equity_curve_plot": output_dir / "equity_curve.png",
        "factor_stats_plot": output_dir / "factor_stats.png",
        "latest_weights_plot": output_dir / "latest_weights.png",
    }

    backtest_result.equity_curve.to_frame().to_csv(paths["equity_curve"], encoding="utf-8-sig")
    backtest_result.weights.to_csv(paths["weights"], encoding="utf-8-sig")
    summary.to_frame("value").to_csv(paths["summary"], encoding="utf-8-sig")
    synthesized_scores.to_csv(paths["synthesized_scores"], encoding="utf-8-sig")

    factor_stats = pd.concat(
        {
            "ic": pd.DataFrame(ic_data),
            "rank_ic": pd.DataFrame(rank_ic_data),
            "icir": pd.DataFrame(icir_data),
        },
        axis=1,
    )
    factor_stats.to_csv(paths["factor_stats"], encoding="utf-8-sig")

    warning_lines = []
    warning_lines.extend(warnings)
    if dropped_factors:
        warning_lines.append("Dropped factors: " + ", ".join(dropped_factors))
    paths["warnings"].write_text("\n".join(warning_lines), encoding="utf-8")

    plot_equity_curve(backtest_result.equity_curve, paths["equity_curve_plot"])
    plot_factor_stats(ic_data, rank_ic_data, icir_data, paths["factor_stats_plot"])
    plot_latest_weights(backtest_result.weights, paths["latest_weights_plot"])
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SimpleQuantDemo research pipeline.")
    parser.add_argument("--db-path", default=None, help="Path to the SQLite database.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = default_research_config(
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    if args.db_path:
        config = replace(config, db_path=Path(args.db_path))
    if args.start_date or args.end_date:
        config = replace(
            config,
            start_date=args.start_date or config.start_date,
            end_date=args.end_date,
        )

    result = run_research(config)
    print("Research pipeline completed.")
    print(result.summary.to_string())
    print("Outputs:")
    for name, path in result.output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
