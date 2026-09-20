"""Research pipeline entrypoint.

Pipeline:
1. Load data from the shared SQLite database (same source as webapp).
2. Build research factors from ``research/factor_config.yaml`` (research pool
   first, core built-ins as fallback) and run independent factor evaluation
   (IC / RankIC / ICIR / collinearity) — for research, not for driving the
   portfolio.
3. Combine category scores into the selected strategy composite (FAA or EAA).
4. Compute sparse target weights on rebalance dates via the unified framework
   (eligibility-checked), then run the single authoritative execution ledger.
5. Compute metrics via the unified metrics implementation and write outputs.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from core.analysis import (
    analyze_collinearity,
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
    calculate_rank_ic,
)
from core.backtest import (
    BacktestConfig,
    BacktestResult,
    ExecutionConfig,
    MetricsConfig,
    calculate_metrics,
)
from core.backtest.engine import execute_backtest
from core.backtest.targets import build_target_weights
from core.calendar import generate_rebalance_dates, get_trading_dates
from core.data import SqliteDataSource
from core.factors.utils import pivot_price_field
from core.synthesis import (
    eaa_composite,
    enabled_category_keys,
    faa_composite,
    filter_issues_by_categories,
)
from core.synthesis.eligibility import build_category_scores_with_details
from research.config import ResearchConfig, default_research_config
from research.factors.config import load_research_categories
from research.factors.registry import resolve_factor_class
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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the FAA or EAA composite score matrix with eligibility details.

    Returns (composite, decision_issues)。合成跨启用类别取有效性 AND；
    decision_issues 供 build_target_weights 记录逐日资格明细——只含启用
    （正权重/正指数）类别，禁用类别的缺失不进决策日志（F19）。
    """
    detail = build_category_scores_with_details(
        price_data,
        universe,
        categories,
        resolver=resolve_factor_class,
    )
    if config.strategy_type == "eaa":
        params = config.exponents or {
            cat.key: 1.0 for cat in categories if not cat.is_empty
        }
        composite = eaa_composite(detail.scores, params, config.beta)
    else:
        params = config.class_weights or {
            cat.key: 1.0 for cat in categories if not cat.is_empty
        }
        composite = faa_composite(detail.scores, params)
    decision_issues = filter_issues_by_categories(
        detail.issues, enabled_category_keys(detail.scores, params)
    )
    return composite, decision_issues


def _slice_backtest_window(
    close: pd.DataFrame,
    composite: pd.DataFrame,
    eval_start: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """F07: 裁剪回测窗口——保留 eval_start 之后全部行情日期。

    旧实现 ``close.index.intersection(composite.index)`` 会删除合成矩阵缺行
    的真实价格日，把 T+1 成交推迟一天、净值序列少一日（与调用处注释
    "never intersected with valid factor dates" 相悖）。改为分数 reindex 到
    行情索引：缺信号日变 NaN，由 build_target_weights 的资格逻辑记
    skipped_insufficient，仅跳过新目标。
    """
    close = close.loc[close.index >= eval_start].sort_index()
    scores = composite.reindex(index=close.index, columns=close.columns).sort_index()
    return close, scores


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

    composite, decision_issues = _build_composite(price_data, universe, categories, config)

    # Backtest window: clip to the evaluation start date; trading dates are the
    # window's date union (never intersected with valid factor dates).
    close = pivot_price_field(price_data, field="close", universe=universe)
    close, scores = _slice_backtest_window(close, composite, eval_start)

    bt_config = BacktestConfig(
        rebalance_freq=config.rebalance_freq,
        top_n=config.top_n,
        max_weight=config.max_weight,
        min_weight=config.min_weight,
        weight_mode=config.weight_mode,
        transaction_cost_bps=config.transaction_cost_bps,
    )
    plan = build_target_weights(
        scores,
        close.index,
        bt_config,
        decision_issues=decision_issues,
    )
    result = execute_backtest(
        close,
        plan.target_weights,
        ExecutionConfig(initial_cash=1.0, transaction_cost_bps=config.transaction_cost_bps),
    )
    result = replace(
        result,
        decision_log=plan.decision_log,
        rebalance_dates=plan.rebalance_dates,
        config_snapshot={
            **result.config_snapshot,
            "selection": {
                "rebalance_freq": bt_config.rebalance_freq,
                "rebalance_day": bt_config.rebalance_day,
                "top_n": bt_config.top_n,
                "max_weight": bt_config.max_weight,
                "min_weight": bt_config.min_weight,
                "weight_mode": bt_config.weight_mode,
            },
        },
    )
    summary = calculate_backtest_summary(result)

    output_paths = write_research_outputs(
        output_dir=config.output_dir,
        backtest_result=result,
        summary=summary,
        ic_data=ic_data,
        rank_ic_data=rank_ic_data,
        icir_data=icir_data,
        synthesized_scores=composite,
        warnings=warnings,
        dropped_factors=[],
    )

    return ResearchRunResult(
        backtest=result,
        selected_factors=list(factor_panel),
        dropped_factors=[],
        warnings=warnings,
        summary=summary,
        output_paths=output_paths,
    )


def calculate_backtest_summary(
    result: BacktestResult,
    annualization: int = 252,
) -> pd.Series:
    """统一指标包装器：只映射 core.backtest.calculate_metrics，不计算任何公式。"""
    return pd.Series(
        calculate_metrics(result, MetricsConfig(annualization=annualization)),
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
    """Write research outputs to CSV/JSON/TXT files.

    weights.csv 为实际日末持仓权重（不再是目标权重）；target_weights.csv /
    trades.csv / decision_log.json 用于交易与资格复核。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "equity_curve": output_dir / "equity_curve.csv",
        "weights": output_dir / "weights.csv",
        "target_weights": output_dir / "target_weights.csv",
        "trades": output_dir / "trades.csv",
        "decision_log": output_dir / "decision_log.json",
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
    backtest_result.target_weights.to_csv(paths["target_weights"], encoding="utf-8-sig")
    backtest_result.trades.to_csv(paths["trades"], index=False, encoding="utf-8-sig")
    decision_log_payload = [
        {
            "decision_date": str(pd.Timestamp(e["decision_date"]).date()),
            "eligible_count": e["eligible_count"],
            "top_n": e["top_n"],
            "status": e["status"],
            "exclusions": [
                {k: (None if v is None else str(v) if not isinstance(v, dict) else v)
                 for k, v in ex.items()}
                for ex in e["exclusions"]
            ],
        }
        for e in backtest_result.decision_log
    ]
    paths["decision_log"].write_text(
        json.dumps(decision_log_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
    parser.add_argument("--rebalance-freq", choices=["weekly", "monthly", "5d"], default=None)
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
