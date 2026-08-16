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

import threading
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from core.factors.config import categories_to_dict, list_factor_categories
from core.optimization import (
    CategoryConstraint as CoreCategoryConstraint,
    EqualWeightOptimizer,
    OptimizationConstraints as CoreOptimizationConstraints,
    ScoreWeightedOptimizer,
    validate_constraints,
)
from research.backtest import BacktestConfig, BacktestResult, run_backtest
from webapp.config import get_config
from webapp.models.constraint_config import ConstraintConfig
from webapp.models.database import utc_now
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
from webapp.services.eaa_faa import build_category_scores, eaa_composite, faa_composite
from webapp.services.universe_service import get_universe_codes, seed_default_universe

# Default date range for backtests when no explicit range is provided.
DEFAULT_START = "2024-01-01"
DEFAULT_END = pd.Timestamp.now().strftime("%Y-%m-%d")

# Factor warm-up window (natural days). Core factors need up to 60 trading days
# of look-back history (momentum_60_reversal / low_vol_60 / ma60_slope_reversal);
# 250 natural days ≈ 170 trading days leaves comfortable margin. Warm-up data is
# used only to compute factor scores, the backtest still starts at ``start_date``
# so rebalance dates are not shifted by the warm-up period.
_WARMUP_DAYS = 250

_TOP_N_OPTIONS = [3, 5, 7, 9]


def _build_meta() -> list[StrategyMeta]:
    """Build strategy metadata from the factor category configuration.

    The category sliders (weights / exponents) are populated from the
    ``factors.yaml`` category list so the UI stays in sync with the config.
    """
    cats = categories_to_dict(include_empty=True)
    non_empty = [c for c in cats if not c["is_empty"]]
    empty_keys = [c["key"] for c in cats if c["is_empty"]]

    # 默认参数来自 research/tune_strategy_params.py 网格搜索
    # （区间 2021-01-04~2026-08-14，5d 调仓，1bp，top_n=5）。
    # FAA：动量0.20/反转0.30/波动0.25/量能0.25；EAA：α 0.5/1/1/1.25，β 0.5。
    default_weights = {"momentum": 0.20, "reversal": 0.30, "volatility": 0.25, "volume": 0.25}
    default_exponents = {"momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25}
    default_beta = 0.5

    # Preserve forward order for the slider rendering.
    slider_options = [{"key": c["key"], "display_name": c["display_name"]} for c in cats]

    return [
        StrategyMeta(
            name="faa",
            display_name="FAA 策略",
            description=(
                "因子分类等权合成类得分，类间按用户配置权重线性加权，"
                "选 Top N 后组内等权配置。"
            ),
            params_schema=[
                StrategyParamSchema(name="top_n", type="int", default=5, min=3, max=9, step=2, label="持仓数量 (Top N)"),
                StrategyParamSchema(name="rebalance_freq", type="str", default="5d", label="调仓频率", options=["weekly", "monthly", "5d"]),
                StrategyParamSchema(name="class_weights", type="category_weights", default=default_weights, label="因子类权重", options=slider_options),
            ],
        ),
        StrategyMeta(
            name="eaa",
            display_name="EAA 策略",
            description=(
                "因子分类等权合成类得分，类得分各自乘方缩放系数 α 后连乘，"
                "整体再乘方 β，得到标准化得分，选 Top N 后按得分占比加权配置。"
            ),
            params_schema=[
                StrategyParamSchema(name="top_n", type="int", default=5, min=3, max=9, step=2, label="持仓数量 (Top N)"),
                StrategyParamSchema(name="rebalance_freq", type="str", default="5d", label="调仓频率", options=["weekly", "monthly", "5d"]),
                StrategyParamSchema(name="exponents", type="category_exponents", default=default_exponents, label="类缩放系数 α", options=slider_options),
                StrategyParamSchema(name="beta", type="float", default=default_beta, min=0.1, max=5.0, step=0.1, label="整体缩放系数 β"),
            ],
        ),
    ]


_STRATEGY_METAS: list[StrategyMeta] = _build_meta()


def list_strategies() -> list[StrategyMeta]:
    """Return metadata for all supported strategies."""
    return _STRATEGY_METAS


def get_strategy_meta(strategy_type: str) -> StrategyMeta | None:
    """Get metadata for a single strategy type."""
    for meta in _STRATEGY_METAS:
        if meta.name == strategy_type:
            return meta
    return None


def _load_core_constraints(db: Session) -> CoreOptimizationConstraints:
    """Load persisted constraints and convert to the core representation."""
    row = db.query(ConstraintConfig).order_by(ConstraintConfig.id).first()
    if row is None or not row.config:
        return CoreOptimizationConstraints()
    data = row.config
    cat_constraints = [
        CoreCategoryConstraint(
            category_key=c["category_key"],
            category_value=c["category_value"],
            min_weight=c.get("min_weight"),
            max_weight=c.get("max_weight"),
            min_count=c.get("min_count"),
            max_count=c.get("max_count"),
        )
        for c in (data.get("category_constraints") or [])
    ]
    return CoreOptimizationConstraints(
        single_min_weight=data.get("single_min_weight"),
        single_max_weight=data.get("single_max_weight"),
        category_constraints=cat_constraints,
        turnover_limit=data.get("turnover_limit"),
    )


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


# Guards concurrent strategy submissions so only one run executes at a time.
_strategy_run_lock = threading.Lock()


def submit_strategy(
    db: Session,
    request: StrategyRunRequest,
) -> StrategyRunSummary:
    """Validate and submit a strategy run, then hand it off to a background thread.

    Cheap validation (unknown strategy / empty universe) fails synchronously;
    otherwise a ``pending`` run record is created and the pipeline runs in
    background. Only one strategy task may be pending/running at a time.
    """
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

    with _strategy_run_lock:
        running = (
            db.query(StrategyRun)
            .filter(StrategyRun.status.in_(["pending", "running"]))
            .first()
        )
        if running is not None:
            return StrategyRunSummary(
                run_id=0,
                strategy_type=strategy_type,
                status="failed",
                error_msg="已有策略任务运行中，请稍候再试",
            )

        run = StrategyRun(
            strategy_type=strategy_type,
            params=params,
            universe_snapshot=universe_codes,
            start_date=start_date,
            end_date=end_date,
            status="pending",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

    run_id = run.id
    thread = threading.Thread(
        target=_execute_run,
        args=(run_id,),
        daemon=True,
    )
    thread.start()

    return StrategyRunSummary(
        run_id=run_id,
        strategy_type=strategy_type,
        status="pending",
        error_msg=None,
    )


def _execute_run(run_id: int) -> None:
    """Background worker: execute a submitted strategy run and persist the result.

    Uses a fresh session (SQLAlchemy sessions must not cross threads). Inputs
    are re-read from the persisted run record so the worker is self-contained.
    """
    from webapp.models.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if run is None:
            return

        run.status = "running"
        db.commit()

        strategy_type = run.strategy_type
        params = run.params or {}
        start_date = run.start_date or DEFAULT_START
        end_date = run.end_date or DEFAULT_END
        universe_codes = run.universe_snapshot or []

        # Fetch data: load a warm-up window before ``start_date`` so factors
        # have look-back history, then clip to the backtest range.
        warmup_start = (
            pd.Timestamp(start_date) - pd.Timedelta(days=_WARMUP_DAYS)
        ).strftime("%Y-%m-%d")
        full_price = get_etf_price(db, universe_codes, warmup_start, end_date)
        if full_price.empty:
            raise ValueError("未获取到任何行情数据")
        price_data = full_price[
            pd.to_datetime(full_price["date"]) >= pd.Timestamp(start_date)
        ].reset_index(drop=True)
        if price_data.empty:
            raise ValueError("回测区间无行情数据")

        # Build classifications from active rules
        classifications = _build_classifications(db)
        constraints = _load_core_constraints(db)

        # Solve the strategy
        if strategy_type == "faa":
            result, violations, latest_weights, latest_data_date = _run_faa(
                params, price_data, full_price, universe_codes, classifications, constraints
            )
        else:  # eaa
            result, violations, latest_weights, latest_data_date = _run_eaa(
                params, price_data, full_price, universe_codes, classifications, constraints
            )

        # Persist success
        run.status = "success"
        run.result_summary = _result_to_dict(
            result, violations, latest_weights, latest_data_date
        )
        run.completed_at = utc_now()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error_msg = str(exc)
            run.completed_at = utc_now()
            db.commit()
    finally:
        try:
            _prune_history_runs(db, get_config().strategy.max_history_runs)
        finally:
            db.close()


def _build_classifications(db: Session) -> dict[str, dict[str, str]]:
    """Apply classification rules and return {sec_code: {category_key: value}}."""
    results = classify_universe(db)
    return {item.sec_code: item.categories for item in results}


# ── Strategy implementations ───────────────────────────────────────────

def _run_faa(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    full_price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
) -> tuple[BacktestResult, list[ConstraintViolationItem], dict[str, float], str]:
    """FAA strategy: weighted sum of normalized category scores, Top-N equal weight.

    ``full_price_data`` includes the warm-up window and is used for factor
    computation; ``price_data`` is the backtest range only (the engine derives
    rebalance dates from it, so the warm-up period never shifts the schedule).
    """
    top_n = int(params.get("top_n", 5))
    rebalance_freq = params.get("rebalance_freq", "5d")
    class_weights = params.get("class_weights", {}) or {}

    categories = list_factor_categories()
    category_scores = build_category_scores(full_price_data, universe, categories)
    composite = faa_composite(category_scores, class_weights)
    latest_weights, latest_data_date = _latest_recommendation(
        composite, EqualWeightOptimizer(top_n=top_n, max_weight=1.0, min_weight=0.0)
    )

    run_result = run_backtest(
        price_data=price_data,
        factor_scores=composite,
        config=BacktestConfig(
            rebalance_freq=rebalance_freq,
            top_n=top_n,
            max_weight=1.0,
            min_weight=0.0,
            weight_mode="equal",
        ),
    )

    violations = _validate_portfolio(
        run_result.weights.iloc[-1],
        classifications,
        constraints,
    )
    return run_result, violations, latest_weights, latest_data_date


def _run_eaa(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    full_price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
) -> tuple[BacktestResult, list[ConstraintViolationItem], dict[str, float], str]:
    """EAA strategy: power-product of normalized category scores, score-weighted Top-N.

    ``full_price_data`` includes the warm-up window and is used for factor
    computation; ``price_data`` is the backtest range only.
    """
    top_n = int(params.get("top_n", 5))
    rebalance_freq = params.get("rebalance_freq", "5d")
    exponents = params.get("exponents", {}) or {}
    beta = float(params.get("beta", 0.5))

    categories = list_factor_categories()
    category_scores = build_category_scores(full_price_data, universe, categories)
    composite = eaa_composite(category_scores, exponents, beta)
    latest_weights, latest_data_date = _latest_recommendation(
        composite, ScoreWeightedOptimizer(top_n=top_n, max_weight=1.0, min_weight=0.0)
    )

    run_result = run_backtest(
        price_data=price_data,
        factor_scores=composite,
        config=BacktestConfig(
            rebalance_freq=rebalance_freq,
            top_n=top_n,
            max_weight=1.0,
            min_weight=0.0,
            weight_mode="score",
        ),
    )

    violations = _validate_portfolio(
        run_result.weights.iloc[-1],
        classifications,
        constraints,
    )
    return run_result, violations, latest_weights, latest_data_date


def _latest_recommendation(
    composite: pd.DataFrame,
    optimizer,
) -> tuple[dict[str, float], str]:
    """Top-N holdings from the most recent factor date (T).

    Based on the latest (database-latest trading day) factor scores, assuming
    execution at the T+1 close. Returns ``({sec_code: weight}, date_str)`` with
    only positive-weight holdings.
    """
    latest = composite.index[-1]
    score_row = composite.loc[latest].dropna()
    weights = optimizer.optimize(score_row)
    holdings = {k: float(v) for k, v in weights.items() if v > 0}
    return holdings, str(pd.Timestamp(latest).date())


def _validate_portfolio(
    weights: pd.Series,
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
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


# ── Result conversion ─────────────────────────────────────────────────

def _result_to_dict(
    result: BacktestResult,
    violations: list[ConstraintViolationItem] | None = None,
    latest_weights: dict[str, float] | None = None,
    latest_data_date: str | None = None,
) -> dict[str, Any]:
    """Serialize a BacktestResult into a JSON-safe dict."""
    return {
        "metrics": _compute_metrics(
            result.equity_curve, result.daily_returns
        ).model_dump(),
        "constraint_violations": [
            {
                "constraint": v.constraint,
                "message": v.message,
                "severity": v.severity,
            }
            for v in (violations or [])
        ],
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
        "latest_weights": latest_weights or {},
        "latest_data_date": latest_data_date,
    }


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


def cleanup_orphaned_runs() -> None:
    """Mark leftover pending/running runs as failed.

    Called at startup so a process restart does not leave strategy tasks stuck
    in a non-terminal state.
    """
    from webapp.models.database import SessionLocal

    db = SessionLocal()
    try:
        orphans = (
            db.query(StrategyRun)
            .filter(StrategyRun.status.in_(["pending", "running"]))
            .all()
        )
        for run in orphans:
            run.status = "failed"
            run.error_msg = "服务重启，任务中断"
            run.completed_at = utc_now()
        db.commit()
    finally:
        db.close()