"""Strategy execution service.

Orchestrates the full pipeline for each strategy type:
1. Load the active universe from the database
2. Fetch price/macro data via the cached data source
3. Compute factors / estimate returns / solve the optimization
4. Run the backtest engine
5. Validate portfolio constraints
6. Persist the run record and return the summary
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from core.analysis.ic import (
    calculate_factor_ic,
    calculate_forward_returns,
    calculate_icir,
)
from core.factors.registry import get_factor_registry
from core.optimization import (
    BLOptimizer,
    MVOptimizer,
    OptimizationConstraints,
    View,
    validate_constraints,
)
from core.synthesis import ICIRWeightedSynthesizer
from research.backtest import BacktestConfig, BacktestResult, run_backtest
from webapp.config import get_config
from webapp.models.strategy_run import StrategyRun
from webapp.schemas.strategy import (
    ConstraintViolationItem,
    StrategyMeta,
    StrategyMetrics,
    StrategyParamSchema,
    StrategyRunRequest,
    StrategyRunSummary,
)
from webapp.services.classification_service import classify_universe
from webapp.services.data_service import get_etf_price
from webapp.services.universe_service import get_universe_codes, seed_default_universe

# Default date range for backtests when no explicit range is provided.
DEFAULT_START = "2024-01-01"
DEFAULT_END = "2026-12-31"

_STRATEGY_METAS: list[StrategyMeta] = [
    StrategyMeta(
        name="linear_factor",
        display_name="线性因子策略",
        description="基于多个因子的合成评分，选取 Top N 等权配置，按周期调仓。",
        params_schema=[
            StrategyParamSchema(name="factors", type="multi_factor", default=[], label="因子列表"),
            StrategyParamSchema(name="top_n", type="int", default=5, min=1, max=20, label="持仓数量"),
            StrategyParamSchema(name="rebalance_freq", type="str", default="weekly", label="调仓频率", options=["weekly", "monthly"]),
            StrategyParamSchema(name="max_weight", type="float", default=0.5, min=0.1, max=1.0, label="单票最大权重"),
            StrategyParamSchema(name="min_weight", type="float", default=0.0, min=0.0, max=0.3, label="单票最小权重"),
            StrategyParamSchema(name="horizon", type="int", default=5, min=1, max=20, label="调仓窗口(交易日)"),
        ],
    ),
    StrategyMeta(
        name="mvo",
        display_name="均值方差优化",
        description="基于历史收益与协方差矩阵的均值方差组合优化。",
        params_schema=[
            StrategyParamSchema(name="objective", type="str", default="max_sharpe", label="优化目标", options=["min_variance", "max_sharpe", "target_return"]),
            StrategyParamSchema(name="max_weight", type="float", default=0.5, min=0.1, max=1.0, label="单票最大权重"),
            StrategyParamSchema(name="min_weight", type="float", default=0.0, min=0.0, max=0.3, label="单票最小权重"),
            StrategyParamSchema(name="lookback", type="int", default=120, min=30, max=500, label="协方差回看窗口"),
            StrategyParamSchema(name="target_return", type="float", default=None, label="目标收益"),
        ],
    ),
    StrategyMeta(
        name="bl",
        display_name="Black-Litterman",
        description="结合市场均衡先验与投资者观点的 Black-Litterman 组合优化。",
        params_schema=[
            StrategyParamSchema(name="max_weight", type="float", default=0.5, min=0.1, max=1.0, label="单票最大权重"),
            StrategyParamSchema(name="min_weight", type="float", default=0.0, min=0.0, max=0.3, label="单票最小权重"),
            StrategyParamSchema(name="tau", type="float", default=0.05, min=0.01, max=0.2, label="先验不确定性"),
            StrategyParamSchema(name="objective", type="str", default="max_sharpe", label="优化目标"),
            StrategyParamSchema(name="views", type="json", default=[], label="观点列表"),
        ],
    ),
]


def list_strategies() -> list[StrategyMeta]:
    """Return metadata for all supported strategies."""
    return _STRATEGY_METAS


def get_strategy_meta(strategy_type: str) -> StrategyMeta | None:
    """Get metadata for a single strategy type."""
    for meta in _STRATEGY_METAS:
        if meta.name == strategy_type:
            return meta
    return None


# ── Run orchestration ──────────────────────────────────────────────────

def _prune_history_runs(db: Session, max_runs: int) -> None:
    """Delete the oldest run records that exceed ``max_runs``.

    Keeps ``strategy_runs`` bounded by deleting the oldest records by id
    (creation order) once the total count goes above the configured limit.
    """
    if max_runs <= 0:
        return

    total = db.query(StrategyRun).count()
    if total <= max_runs:
        return

    excess_ids = (
        db.query(StrategyRun.id).order_by(StrategyRun.id.asc()).limit(total - max_runs)
    )
    ids = [row[0] for row in excess_ids]
    if ids:
        db.query(StrategyRun).filter(StrategyRun.id.in_(ids)).delete(
            synchronize_session=False
        )
        db.commit()


def run_strategy(
    db: Session,
    request: StrategyRunRequest,
) -> StrategyRunSummary:
    """Execute a strategy and persist the run record."""
    seed_default_universe(db)
    universe_codes = get_universe_codes(db)
    if not universe_codes:
        return StrategyRunSummary(
            run_id=0,
            strategy_type=request.strategy_type,
            status="failed",
            error_msg="标的池为空，请先添加标的",
        )

    strategy_type = request.strategy_type.lower()
    if get_strategy_meta(strategy_type) is None:
        return StrategyRunSummary(
            run_id=0,
            strategy_type=strategy_type,
            status="failed",
            error_msg=f"未知策略类型: {strategy_type}",
        )

    start_date = request.start_date or DEFAULT_START
    end_date = request.end_date or DEFAULT_END
    params = request.params or {}

    run = StrategyRun(
        strategy_type=strategy_type,
        params=params,
        universe_snapshot=universe_codes,
        start_date=start_date,
        end_date=end_date,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        # Fetch data
        price_data = get_etf_price(db, universe_codes, start_date, end_date)
        if price_data.empty:
            raise ValueError("未获取到任何行情数据")

        # Build classifications from active rules
        classifications = _build_classifications(db)

        # Solve the strategy
        if strategy_type == "linear_factor":
            result, violations = _run_linear_factor(
                params, price_data, universe_codes, classifications
            )
        elif strategy_type == "mvo":
            result, violations = _run_mvo(
                params, price_data, universe_codes, classifications
            )
        else:  # bl
            result, violations = _run_bl(
                params, price_data, universe_codes, classifications
            )

        # Persist success
        run.status = "success"
        run.result_summary = _result_to_dict(result)
        run.completed_at = datetime.utcnow()
        db.commit()

        return _to_summary(run.id, strategy_type, result, violations)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.query(StrategyRun).filter(StrategyRun.id == run.id).first()
        if run:
            run.status = "failed"
            run.error_msg = str(exc)
            run.completed_at = datetime.utcnow()
            db.commit()
        return StrategyRunSummary(
            run_id=run.id if run else 0,
            strategy_type=strategy_type,
            status="failed",
            error_msg=str(exc),
        )
    finally:
        _prune_history_runs(db, get_config().strategy.max_history_runs)


def _build_classifications(db: Session) -> dict[str, dict[str, str]]:
    """Apply classification rules and return {sec_code: {category_key: value}}."""
    results = classify_universe(db)
    return {item.sec_code: item.categories for item in results}


# ── Strategy implementations ───────────────────────────────────────────

def _run_linear_factor(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
) -> tuple[BacktestResult, list[ConstraintViolationItem]]:
    """Linear-factor strategy: synthesize factor scores, select Top-N, backtest."""
    factor_names = params.get("factors") or ["momentum", "volatility", "reversal"]
    top_n = int(params.get("top_n", 5))
    max_weight = float(params.get("max_weight", 0.5))
    min_weight = float(params.get("min_weight", 0.0))
    horizon = int(params.get("horizon", 5))
    rebalance_freq = params.get("rebalance_freq", "weekly")

    # Build factor panel
    factor_panel: dict[str, pd.DataFrame] = {}
    registry = get_factor_registry()
    for name in factor_names:
        cls = registry.get(name)
        if cls is None:
            raise ValueError(f"因子不存在: {name}")
        factor_panel[name] = cls().build(price_data, pd.DataFrame(), universe)

    # ICIR-weighted synthesis
    forward_returns = calculate_forward_returns(
        price_data, horizon=horizon, universe=universe
    )
    icir_data = {}
    for fname, fmat in factor_panel.items():
        ic_series = calculate_factor_ic(
            fmat.dropna(how="all"), forward_returns, min_observations=10
        )
        icir_data[fname] = calculate_icir(ic_series, window=20, min_periods=10)
    synthesized = ICIRWeightedSynthesizer().synthesize(factor_panel, icir_data)
    composite = synthesized.dropna(how="all")

    # Category count constraints from classifications (min_count/max_count
    # are inferred by membership; here we only wire the declared bounds).
    constraints = OptimizationConstraints(
        single_min_weight=min_weight,
        single_max_weight=max_weight,
    )

    run_result = run_backtest(
        price_data=price_data,
        factor_scores=composite,
        config=BacktestConfig(
            rebalance_freq=rebalance_freq,
            top_n=top_n,
            max_weight=max_weight,
            min_weight=min_weight,
        ),
    )

    violations = _validate_portfolio(
        run_result.weights.iloc[-1],
        classifications,
        constraints,
    )
    return run_result, violations


def _validate_portfolio(
    weights: pd.Series,
    classifications: dict[str, dict[str, str]],
    constraints: OptimizationConstraints,
) -> list[ConstraintViolationItem]:
    violations = validate_constraints(
        weights.to_dict(),
        classifications,
        constraints,
    )
    return [
        ConstraintViolationItem(
            constraint=v.constraint,
            message=v.message,
            severity=v.severity,
        )
        for v in violations
    ]


def _run_mvo(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
) -> tuple[BacktestResult, list[ConstraintViolationItem]]:
    """MVO strategy: estimate returns/cov, solve MVO, backtest."""
    objective = params.get("objective", "max_sharpe")
    max_weight = float(params.get("max_weight", 0.5))
    min_weight = float(params.get("min_weight", 0.0))
    target_return = params.get("target_return")

    close = price_data.pivot_table(index="date", columns="sec", values="close")
    close = close[universe].sort_index()
    returns = close.pct_change(fill_method=None).dropna()

    # Estimate annualized expected returns and covariance.
    exp_ret = returns.mean() * 252
    cov = returns.cov() * 252

    constraints = OptimizationConstraints(
        single_min_weight=min_weight,
        single_max_weight=max_weight,
    )

    optimizer = MVOptimizer(
        objective=objective,
        max_weight=max_weight,
        min_weight=min_weight,
        target_return=target_return,
        constraints=constraints,
    )

    weights = optimizer.optimize(
        expected_returns=exp_ret,
        cov_matrix=cov,
        classifications=classifications,
    )

    # Hold the single-period MVO target for the backtest window.
    score_matrix = pd.DataFrame(
        weights.values[np.newaxis, :].repeat(len(close.index), axis=0),
        index=close.index,
        columns=close.columns,
    )

    run_result = run_backtest(
        price_data=price_data,
        factor_scores=score_matrix,
        config=BacktestConfig(
            rebalance_freq="monthly",
            top_n=len(universe),
            max_weight=max_weight,
            min_weight=min_weight,
        ),
    )
    violations = _validate_portfolio(weights, classifications, constraints)
    return run_result, violations


def _run_bl(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
) -> tuple[BacktestResult, list[ConstraintViolationItem]]:
    """BL strategy: BL posterior estimates -> MVO -> backtest."""
    max_weight = float(params.get("max_weight", 0.5))
    min_weight = float(params.get("min_weight", 0.0))
    tau = float(params.get("tau", 0.05))
    objective = params.get("objective", "max_sharpe")
    raw_views = params.get("views", []) or []

    close = price_data.pivot_table(index="date", columns="sec", values="close")
    close = close[universe].sort_index()
    returns = close.pct_change(fill_method=None).dropna()

    exp_ret = returns.mean() * 252
    cov = returns.cov() * 252

    # Proxy market caps: use total traded amount as a stand-in if available.
    if "amount" in price_data.columns:
        caps = (
            price_data.groupby("sec")["amount"]
            .sum()
            .reindex(universe)
            .fillna(1.0)
            .astype(float)
        )
    else:
        caps = pd.Series(1.0, index=universe)

    # Build View objects
    views: list[View] = []
    for v in raw_views:
        views.append(
            View(
                assets=[
                    (a["sec"], float(a.get("weight", 1.0)))
                    for a in v.get("assets", [])
                ],
                q=float(v.get("q", 0.0)),
                confidence=float(v.get("confidence", 1.0)),
            )
        )

    constraints = OptimizationConstraints(
        single_min_weight=min_weight,
        single_max_weight=max_weight,
    )

    bl_opt = BLOptimizer(
        market_caps=caps,
        cov_matrix=cov,
        views=views if views else None,
        tau=tau,
        objective=objective,
        max_weight=max_weight,
        min_weight=min_weight,
        constraints=constraints,
    )
    weights = bl_opt.optimize(classifications=classifications)

    score_matrix = pd.DataFrame(
        weights.values[np.newaxis, :].repeat(len(close.index), axis=0),
        index=close.index,
        columns=close.columns,
    )

    run_result = run_backtest(
        price_data=price_data,
        factor_scores=score_matrix,
        config=BacktestConfig(
            rebalance_freq="monthly",
            top_n=len(universe),
            max_weight=max_weight,
            min_weight=min_weight,
        ),
    )
    violations = _validate_portfolio(weights, classifications, constraints)
    return run_result, violations


# ── Result conversion ─────────────────────────────────────────────────

def _result_to_dict(result: BacktestResult) -> dict[str, Any]:
    """Serialize a BacktestResult into a JSON-safe dict."""
    return {
        "equity_curve": {
            str(k.date()): float(v) for k, v in result.equity_curve.dropna().items()
        },
        "daily_returns": {
            str(k.date()): float(v) for k, v in result.daily_returns.dropna().items()
        },
        "weights": {
            str(k.date()): {
                str(col): float(v)
                for col, v in row.dropna().items()
            }
            for k, row in result.weights.iterrows()
        },
        "turnover": {
            str(k.date()): float(v) for k, v in result.turnover.dropna().items()
        },
        "costs": {
            str(k.date()): float(v) for k, v in result.costs.dropna().items()
        },
        "rebalance_dates": [
            str(d.date()) for d in result.rebalance_dates
        ],
    }


def _to_summary(
    run_id: int,
    strategy_type: str,
    result: BacktestResult,
    violations: list[ConstraintViolationItem],
) -> StrategyRunSummary:
    """Convert a backtest result into an API summary."""
    equity = result.equity_curve
    daily_returns = result.daily_returns

    metrics = _compute_metrics(equity, daily_returns)

    # Latest weights
    latest_weights = result.weights.iloc[-1].dropna()
    latest_weights = latest_weights[latest_weights > 0]
    weights_dict = {
        str(k): float(v) for k, v in latest_weights.items()
    }

    return StrategyRunSummary(
        run_id=run_id,
        strategy_type=strategy_type,
        status="success",
        metrics=metrics,
        nav_series={
            str(k.date()): float(v) for k, v in equity.dropna().items()
        },
        weights=weights_dict,
        constraint_violations=violations,
    )


def _compute_metrics(equity: pd.Series, daily_returns: pd.Series) -> StrategyMetrics:
    """Compute standard performance metrics from a NAV/return series."""
    if equity.empty or len(equity) < 2:
        return StrategyMetrics(
            total_return=0.0,
            annual_return=0.0,
            annual_volatility=0.0,
            sharpe=0.0,
            max_drawdown=0.0,
        )

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    n_days = len(equity)
    annual_return = (
        float((1 + total_return) ** (252 / n_days) - 1)
        if total_return > -1
        else -1.0
    )
    vol = float(daily_returns.std() * np.sqrt(252))
    sharpe = float((annual_return - 0.02) / vol) if vol > 0 else 0.0

    # Max drawdown
    cummax = equity.cummax()
    drawdown = equity / cummax - 1
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    return StrategyMetrics(
        total_return=round(total_return, 6),
        annual_return=round(annual_return, 6),
        annual_volatility=round(vol, 6),
        sharpe=round(sharpe, 6),
        max_drawdown=round(max_drawdown, 6),
    )


def list_runs(db: Session, limit: int = 20) -> list[StrategyRun]:
    """Return the most recent strategy run records."""
    return (
        db.query(StrategyRun)
        .order_by(StrategyRun.id.desc())
        .limit(limit)
        .all()
    )


def get_run(db: Session, run_id: int) -> StrategyRun | None:
    """Get a single run record by ID."""
    return db.query(StrategyRun).filter(StrategyRun.id == run_id).first()