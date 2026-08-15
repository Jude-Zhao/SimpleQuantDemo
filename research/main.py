"""Research pipeline entrypoint.

Pipeline:
1. Load data from the shared SQLite database (same source as webapp).
2. Build research factors from ``research/factor_config.yaml`` (research pool
   first, core built-ins as fallback) and run independent factor evaluation
   (IC / RankIC / ICIR / collinearity) — for research, not for driving the
   portfolio.
3. Combine category scores into the selected strategy composite (FAA or EAA).
4. Compute target weights on rebalance dates with the core optimizers.
5. Run the bt-based backtest and produce quantstats-based metrics + outputs.
"""

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
from core.factors.utils import pivot_price_field
from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer
from core.synthesis import build_category_scores, eaa_composite, faa_composite
from research.bt_engine import BTBacktestResult, run_bt_backtest
from research.config import ResearchConfig, default_research_config
from research.factors.config import load_research_categories
from research.factors.registry import resolve_factor_class
from research.visualization import plot_equity_curve, plot_factor_stats, plot_latest_weights


@dataclass(frozen=True)
class ResearchRunResult:
    """Outputs from one research pipeline run."""

    backtest: BTBacktestResult
    selected_factors: list[str]
    dropped_factors: list[str]
    warnings: list[str]
    summary: pd.Series
    output_paths: dict[str, Path]


def _build_factor_panel(
    price_data: pd.DataFrame,
    universe: list[str],
    categories: tuple,
) -> dict[str, pd.DataFrame]:
    """Build every research factor instance into a {name: matrix} panel."""
    panel: dict[str, pd.DataFrame] = {}
    for cat in categories:
        if cat.is_empty:
            continue
        for inst in cat.factors:
            cls = resolve_factor_class(inst.name)
            if cls is None:
                continue
            builder = cls(**inst.params)
            factor = builder.build(price_data, pd.DataFrame(), universe)
            panel[builder.name] = factor
    return panel


def _evaluate_factors(
    price_data: pd.DataFrame,
    factor_panel: dict[str, pd.DataFrame],
    config: ResearchConfig,
    eval_start: pd.Timestamp | None = None,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], dict[str, pd.Series], list[str]]:
    """Run independent factor evaluation (IC / RankIC / ICIR / collinearity).

    Returns (ic_data, rank_ic_data, icir_data, warnings). This is research
    output only and does not drive the composite strategy. If ``eval_start``
    is given, IC is only evaluated on rebalance dates >= eval_start.
    """
    forward_returns = calculate_forward_returns(
        price_data,
        horizon=config.forward_return_horizon,
        universe=list(factor_panel[next(iter(factor_panel))].columns) if factor_panel else [],
    )
    ic_dates = generate_rebalance_dates(
        trading_dates=get_trading_dates(price_data).intersection(forward_returns.index),
        rebalance_freq=config.rebalance_freq,
        rebalance_day=0,
    )
    if eval_start is not None:
        ic_dates = ic_dates[ic_dates >= eval_start]

    ic_data: dict[str, pd.Series] = {}
    rank_ic_data: dict[str, pd.Series] = {}
    for factor_name, factor in factor_panel.items():
        ic_series = calculate_factor_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_observations=config.ic_min_observations,
        )
        ic_data[factor_name] = ic_series.loc[ic_series.index.intersection(ic_dates)]
        rank_series = calculate_rank_ic(
            factor=factor,
            forward_returns=forward_returns,
            min_observations=config.ic_min_observations,
        )
        rank_ic_data[factor_name] = rank_series.loc[rank_series.index.intersection(ic_dates)]

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
        mode="warn",
        min_observations=config.ic_min_observations,
    )
    return ic_data, rank_ic_data, icir_data, collinearity.warnings


def _build_composite(
    price_data: pd.DataFrame,
    universe: list[str],
    categories: tuple,
    config: ResearchConfig,
) -> pd.DataFrame:
    """Build the FAA or EAA composite score matrix."""
    category_scores = build_category_scores(
        price_data,
        universe,
        categories,
        resolver=resolve_factor_class,
    )
    if config.strategy_type == "eaa":
        exponents = config.exponents or {
            cat.key: 1.0 for cat in categories if not cat.is_empty
        }
        return eaa_composite(category_scores, exponents, config.beta)
    class_weights = config.class_weights or {
        cat.key: 1.0 for cat in categories if not cat.is_empty
    }
    return faa_composite(category_scores, class_weights)


def _build_target_weights(
    close: pd.DataFrame,
    scores: pd.DataFrame,
    config: ResearchConfig,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Compute target weights on rebalance dates and forward-fill."""
    rebalance_dates = generate_rebalance_dates(
        trading_dates=close.index,
        rebalance_freq=config.rebalance_freq,
        rebalance_day=0,
    )
    optimizer = (
        ScoreWeightedOptimizer(
            top_n=config.top_n,
            max_weight=config.max_weight,
            min_weight=config.min_weight,
        )
        if config.weight_mode == "score"
        else EqualWeightOptimizer(
            top_n=config.top_n,
            max_weight=config.max_weight,
            min_weight=config.min_weight,
        )
    )
    target_weights = pd.DataFrame(
        pd.NA, index=close.index, columns=close.columns, dtype="Float64"
    )
    for date in rebalance_dates:
        if date not in scores.index:
            continue
        score_row = scores.loc[date].dropna()
        # Skip rebalance dates with fewer eligible securities than top_n
        # (e.g. during data warm-up when many factors are still NaN).
        if len(score_row) < config.top_n:
            continue
        target_weights.loc[date] = optimizer.optimize(score_row)

    filled = target_weights.ffill().fillna(0.0).astype(float)
    return filled, rebalance_dates


def run_research(config: ResearchConfig) -> ResearchRunResult:
    """Run the research pipeline."""
    data_source = SqliteDataSource(db_path=config.db_path)
    # Load data from ``data_start_date`` (warm-up) when set, so factors with
    # long windows (e.g. 120d) have look-back history. Evaluation/backtest
    # still begin at ``config.start_date``.
    load_start = config.data_start_date or config.start_date
    price_data, _macro_data, universe = data_source.load_all(
        start_date=load_start,
        end_date=config.end_date,
    )

    categories = load_research_categories()
    factor_panel = _build_factor_panel(price_data, universe, categories)

    eval_start = pd.Timestamp(config.start_date)
    ic_data, rank_ic_data, icir_data, warnings = _evaluate_factors(
        price_data, factor_panel, config, eval_start=eval_start
    )

    composite = _build_composite(price_data, universe, categories, config)

    close = pivot_price_field(price_data, field="close", universe=universe)
    close = close.loc[
        close.index.intersection(composite.index), composite.columns
    ].sort_index()
    # Backtest only from the evaluation start date onward.
    close = close.loc[close.index >= eval_start]
    scores = composite.loc[close.index, close.columns].sort_index()

    target_weights, _rebalance_dates = _build_target_weights(close, scores, config)
    backtest_result = run_bt_backtest(
        close=close,
        target_weights=target_weights,
        rebalance_freq=config.rebalance_freq,
    )
    summary = calculate_backtest_summary(backtest_result)

    output_paths = write_research_outputs(
        output_dir=config.output_dir,
        backtest_result=backtest_result,
        summary=summary,
        ic_data=ic_data,
        rank_ic_data=rank_ic_data,
        icir_data=icir_data,
        synthesized_scores=composite,
        warnings=warnings,
        dropped_factors=[],
    )

    return ResearchRunResult(
        backtest=backtest_result,
        selected_factors=list(factor_panel),
        dropped_factors=[],
        warnings=warnings,
        summary=summary,
        output_paths=output_paths,
    )


def calculate_backtest_summary(
    result: BTBacktestResult,
    annualization: int = 252,
) -> pd.Series:
    """Calculate performance metrics with quantstats.

    Annual return / volatility / Sharpe / drawdown / Sortino / Calmar / win
    rate are delegated to ``quantstats``. Turnover and rebalance counts are
    derived from the target-weight matrix held by ``result.weights``. The bt
    engine is not configured with a commission model, so ``cost_sum`` is 0.
    """
    returns = result.daily_returns.astype(float)
    equity_curve = result.equity_curve.astype(float)
    total_return = equity_curve.iloc[-1] - 1.0
    turnover = result.weights.diff().abs().sum(axis=1)
    turnover_sum = float(turnover.sum())
    rebalance_count = int((turnover > 0).sum())

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
            "turnover_sum": turnover_sum,
            "cost_sum": 0.0,
            "rebalance_count": rebalance_count,
        },
        name="summary",
    )


def write_research_outputs(
    output_dir: Path,
    backtest_result: BTBacktestResult,
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

    backtest_result.equity_curve.reset_index().rename(
        columns={"index": "date", "equity": "equity"}
    ).to_csv(paths["equity_curve"], index=False, encoding="utf-8-sig")
    backtest_result.weights.to_csv(paths["weights"], encoding="utf-8-sig")
    summary.rename("value").reset_index().to_csv(
        paths["summary"], index=False, encoding="utf-8-sig"
    )
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

    warning_lines = list(warnings)
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
    parser.add_argument(
        "--data-start-date",
        default=None,
        help="Data loading start (warm-up). Defaults to --start-date. Set earlier "
        "than --start-date to give long-window factors look-back history.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--strategy", choices=["faa", "eaa"], default=None)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--rebalance-freq", choices=["weekly", "monthly"], default=None)
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
    if args.data_start_date:
        config = replace(config, data_start_date=args.data_start_date)
    if args.strategy:
        config = replace(
            config,
            strategy_type=args.strategy,
            weight_mode="score" if args.strategy == "eaa" else "equal",
        )
    if args.top_n is not None:
        config = replace(config, top_n=args.top_n)
    if args.rebalance_freq:
        config = replace(config, rebalance_freq=args.rebalance_freq)

    result = run_research(config)
    print("Research pipeline completed.")
    print(result.summary.to_string())
    print("Outputs:")
    for name, path in result.output_paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()