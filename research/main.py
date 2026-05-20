"""Research pipeline entrypoint."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.analysis import (
    analyze_collinearity,
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
    calculate_rank_ic,
)
from core.data import CsvDataSource
from core.factors import MomentumFactor, VolatilityFactor
from core.synthesis import ICIRWeightedSynthesizer
from research.backtest import BacktestResult, run_backtest
from research.config import ResearchConfig, default_research_config


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

    ic_data = {
        factor_name: calculate_factor_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_periods=config.ic_min_periods,
        )
        for factor_name, factor in factor_panel.items()
    }
    rank_ic_data = {
        factor_name: calculate_rank_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_periods=config.ic_min_periods,
        )
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
        min_periods=config.ic_min_periods,
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
    """Calculate basic performance statistics."""
    daily_returns = result.daily_returns.astype(float)
    equity_curve = result.equity_curve.astype(float)
    total_return = equity_curve.iloc[-1] - 1.0
    periods = max(len(daily_returns), 1)
    annual_return = equity_curve.iloc[-1] ** (annualization / periods) - 1.0
    annual_volatility = daily_returns.std() * (annualization ** 0.5)
    sharpe = annual_return / annual_volatility if annual_volatility and annual_volatility > 0 else float("nan")
    drawdown = equity_curve / equity_curve.cummax() - 1.0

    return pd.Series(
        {
            "total_return": total_return,
            "annual_return": annual_return,
            "annual_volatility": annual_volatility,
            "sharpe": sharpe,
            "max_drawdown": drawdown.min(),
            "win_rate": (daily_returns > 0).mean(),
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
    return paths


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SimpleQuantDemo research pipeline.")
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    config = default_research_config(
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    if args.start_date or args.end_date:
        config = ResearchConfig(
            etf_price_path=config.etf_price_path,
            macro_factors_path=config.macro_factors_path,
            universe_path=config.universe_path,
            output_dir=config.output_dir,
            start_date=args.start_date or config.start_date,
            end_date=args.end_date,
            momentum_window=config.momentum_window,
            volatility_window=config.volatility_window,
            forward_return_horizon=config.forward_return_horizon,
            ic_min_periods=config.ic_min_periods,
            icir_window=config.icir_window,
            icir_min_periods=config.icir_min_periods,
            half_life_periods=config.half_life_periods,
            collinearity_threshold=config.collinearity_threshold,
            backtest=config.backtest,
        )

    result = run_research(config)
    print("Research pipeline completed.")
    print(result.summary.to_string())
    print("Outputs:")
    for name, path in result.output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()

