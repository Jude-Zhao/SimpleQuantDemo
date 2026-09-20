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

import logging
import threading
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from core.backtest import BacktestConfig, BacktestResult, calculate_metrics, run_backtest
from core.factors.config import categories_to_dict, list_factor_categories
from core.optimization import (
    CategoryConstraint as CoreCategoryConstraint,
    EqualWeightOptimizer,
    OptimizationConstraints as CoreOptimizationConstraints,
    OptimizationError,
    ScoreWeightedOptimizer,
    validate_constraints,
)
from core.synthesis import (
    eaa_composite,
    enabled_category_keys,
    faa_composite,
    filter_issues_by_categories,
)
from core.synthesis.eligibility import build_category_scores_with_details
from webapp.config import get_config
from webapp.models.database import utc_now
from webapp.models.strategy_run import StrategyRun
from webapp.schemas.strategy import (
    ConstraintCheckItem,
    ConstraintViolationItem,
    DecisionLogItem,
    ExecutionLogItem,
    RunContext,
    RunDiagnostics,
    StrategyMeta,
    StrategyMetrics,
    StrategyParamSchema,
    StrategyRunRequest,
    StrategyRunSummary,
)
from webapp.services import constraint_service
from webapp.services.classification_service import classify_universe
from webapp.services.data_service import get_etf_price
from webapp.services.universe_service import get_universe_codes, list_active_universe, seed_default_universe

# Default date range for backtests when no explicit range is provided.
DEFAULT_START = "2024-01-01"


def default_end_date() -> str:
    """Default end date for backtests/rankings, evaluated per call so a long-lived
    process always tracks the current date instead of freezing at startup."""
    return pd.Timestamp.now().strftime("%Y-%m-%d")

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

    # 默认参数来自 research/tune_strategy_params.py 网格搜索
    # （区间 2021-01-04~2026-08-14，5d 调仓，1bp，top_n=5）。
    # FAA：动量0.20/反转0.30/波动0.25/量能0.25；EAA：α 0.5/1/1/1.25，β 0.5。
    default_weights = {"momentum": 0.20, "reversal": 0.30, "volatility": 0.25, "volume": 0.25}
    default_exponents = {"momentum": 0.5, "reversal": 1.0, "volatility": 1.0, "volume": 1.25}
    default_beta = 0.5

    # 策略滑杆只列非空类别：空类别（factors: []）不参与得分合成，若可启用会
    # 触发资格校验失败（EAA 滑杆默认 α=1 且最小 0.1，无法归零）。空类别加入
    # 因子后随 factors.yaml 自动出现在滑杆中。
    slider_options = [{"key": c["key"], "display_name": c["display_name"]} for c in non_empty]

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


def load_core_constraints(db: Session) -> CoreOptimizationConstraints:
    """Load persisted constraints (via constraint_service) as the core representation."""
    web = constraint_service.load_constraints(db)
    cat_constraints = [
        CoreCategoryConstraint(
            category_key=c.category_key,
            category_value=c.category_value,
            min_weight=c.min_weight,
            max_weight=c.max_weight,
            min_count=c.min_count,
            max_count=c.max_count,
        )
        for c in web.category_constraints
    ]
    return CoreOptimizationConstraints(
        single_min_weight=web.single_min_weight,
        single_max_weight=web.single_max_weight,
        category_constraints=cat_constraints,
        turnover_limit=web.turnover_limit,
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

logger = logging.getLogger(__name__)


def _mark_run_failed(run_id: int, error_msg: str) -> None:
    """尽力把运行记录置为 failed 终态（自建会话）。

    用于线程启动失败、worker 会话创建失败等走不到正常错误处理分支的路径：
    pending 记录不终结会永久占用策略任务互斥。标记本身依赖数据库可用，
    再次失败时只能记录日志并放弃。
    """
    from webapp.models.database import SessionLocal

    try:
        db = SessionLocal()
    except Exception:
        logger.exception("策略任务 %s 标记 failed 时创建会话失败", run_id)
        return
    try:
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error_msg = error_msg
            run.completed_at = utc_now()
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("策略任务 %s 标记 failed 失败", run_id)
    finally:
        db.close()


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
    end_date = request.end_date or default_end_date()
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
    try:
        thread = threading.Thread(
            target=_execute_run,
            args=(run_id,),
            daemon=True,
        )
        thread.start()
    except Exception as exc:  # noqa: BLE001
        # pending 记录已提交：启动失败必须立即置为终态，否则互斥检查会
        # 永久拒绝后续提交，直到重启清理。
        error_msg = f"策略任务线程启动失败: {exc}"
        db.rollback()
        failed = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if failed:
            failed.status = "failed"
            failed.error_msg = error_msg
            failed.completed_at = utc_now()
            db.commit()
        return StrategyRunSummary(
            run_id=run_id,
            strategy_type=strategy_type,
            status="failed",
            error_msg=error_msg,
        )

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

    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001
        # 会话创建失败时记录仍停留在 pending，会永久占用策略任务互斥；
        # 用新会话尽力置为终态。
        _mark_run_failed(run_id, f"创建数据库会话失败: {exc}")
        return
    try:
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        if run is None:
            return

        run.status = "running"
        db.commit()

        strategy_type = run.strategy_type
        params = run.params or {}
        start_date = run.start_date or DEFAULT_START
        end_date = run.end_date or default_end_date()
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
        constraints = load_core_constraints(db)

        # Freeze run context at computation start (B7)：有效策略参数、名称
        # 映射、实际因子实例/类别、分类结果与有效约束。
        active_items = list_active_universe(db)
        sec_names = {
            item.sec_code: (item.sec_name or item.sec_code) for item in active_items
        }
        for code in universe_codes:
            sec_names.setdefault(code, code)
        run_context = RunContext(
            strategy_type=strategy_type,
            params=params,
            sec_names=sec_names,
            factor_categories=categories_to_dict(include_empty=True),
            classifications=classifications,
            constraints=constraint_service.load_constraints(db).model_dump(),
        )

        # Solve the strategy
        if strategy_type == "faa":
            result, latest_weights, latest_data_date, latest_reason = _run_faa(
                params, price_data, full_price, universe_codes, classifications, constraints
            )
        else:  # eaa
            result, latest_weights, latest_data_date, latest_reason = _run_eaa(
                params, price_data, full_price, universe_codes, classifications, constraints
            )

        # Persist success
        run.status = "success"
        run.result_summary = _result_to_dict(
            result=result,
            run_context=run_context,
            constraints=constraints,
            latest_weights=latest_weights,
            latest_data_date=latest_data_date,
            latest_reason=latest_reason,
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
) -> tuple[BacktestResult, dict[str, float], str, str | None]:
    """FAA strategy: weighted sum of normalized category scores, Top-N equal weight.

    ``full_price_data`` includes the warm-up window and is used for factor
    computation; ``price_data`` is the backtest range only (the engine derives
    rebalance dates from it, so the warm-up period never shifts the schedule).
    Returns (result, latest_weights, latest_data_date, latest_recommendation_reason)。
    """
    top_n = int(params.get("top_n", 5))
    rebalance_freq = params.get("rebalance_freq", "5d")
    class_weights = params.get("class_weights", {}) or {}

    categories = list_factor_categories()
    detail = build_category_scores_with_details(full_price_data, universe, categories)
    composite = faa_composite(detail.scores, class_weights)
    # F19: 与合成共用启用集合，禁用类别的资格缺失不进决策日志排除项
    decision_issues = filter_issues_by_categories(
        detail.issues, enabled_category_keys(detail.scores, class_weights)
    )
    latest_weights, latest_data_date, latest_reason = _latest_recommendation(
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
        decision_issues=decision_issues,
    )

    return run_result, latest_weights, latest_data_date, latest_reason


def _run_eaa(
    params: dict[str, Any],
    price_data: pd.DataFrame,
    full_price_data: pd.DataFrame,
    universe: list[str],
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
) -> tuple[BacktestResult, dict[str, float], str, str | None]:
    """EAA strategy: power-product of normalized category scores, score-weighted Top-N.

    ``full_price_data`` includes the warm-up window and is used for factor
    computation; ``price_data`` is the backtest range only.
    Returns (result, latest_weights, latest_data_date, latest_recommendation_reason)。
    """
    top_n = int(params.get("top_n", 5))
    rebalance_freq = params.get("rebalance_freq", "5d")
    exponents = params.get("exponents", {}) or {}
    beta = float(params.get("beta", 0.5))

    categories = list_factor_categories()
    detail = build_category_scores_with_details(full_price_data, universe, categories)
    composite = eaa_composite(detail.scores, exponents, beta)
    # F19: 与合成共用启用集合，禁用类别的资格缺失不进决策日志排除项
    decision_issues = filter_issues_by_categories(
        detail.issues, enabled_category_keys(detail.scores, exponents)
    )
    latest_weights, latest_data_date, latest_reason = _latest_recommendation(
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
        decision_issues=decision_issues,
    )

    return run_result, latest_weights, latest_data_date, latest_reason


def _latest_recommendation(
    composite: pd.DataFrame,
    optimizer,
) -> tuple[dict[str, float], str, str | None]:
    """Top-N holdings from the most recent factor date (T).

    Based on the latest (database-latest trading day) factor scores, assuming
    execution at the T+1 close. Returns ``(holdings, date_str, reason)``：
    没有足够合格证券时返回空持仓并明确理由，不从过去某日回填旧推荐。
    """
    latest = composite.index[-1]
    score_row = composite.loc[latest].dropna()
    try:
        weights = optimizer.optimize(score_row)
    except OptimizationError as exc:
        return (
            {},
            str(pd.Timestamp(latest).date()),
            f"最新因子日({pd.Timestamp(latest).date()})合格证券不足，无法生成推荐: {exc}",
        )
    holdings = {k: float(v) for k, v in weights.items() if v > 0}
    return holdings, str(pd.Timestamp(latest).date()), None


def _violation_to_item(v: Any) -> ConstraintViolationItem:
    """core ConstraintViolation → schema（携带结构化 actual/limit/unit）。"""
    return ConstraintViolationItem(
        constraint=v.constraint,
        message=v.message,
        severity=v.severity,
        target=getattr(v, "target", None),
        actual=getattr(v, "actual", None),
        limit=getattr(v, "limit", None),
        unit=getattr(v, "unit", None),
    )


def _check_weights(
    weights: dict[str, float],
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
) -> list[ConstraintViolationItem]:
    """权重约束检查（含结构化字段），不影响原组合。"""
    return [_violation_to_item(v) for v in validate_constraints(weights, classifications, constraints)]


def _build_constraint_checks(
    result: BacktestResult,
    classifications: dict[str, dict[str, str]],
    constraints: CoreOptimizationConstraints,
    latest_weights: dict[str, float],
    latest_data_date: str | None,
    latest_reason: str | None,
) -> list[ConstraintCheckItem]:
    """B7 约束检查：逐实际执行日检查历史；最新推荐单独检查。

    - 换手使用权威框架口径（result.turnover），按当日实际成交检查；
    - latest 没有确定未来成交价 → turnover 标 not_evaluated，不能用目标
      权重差冒充实际换手；
    - 检查是纯读取，不改变原组合、交易与净值。
    """
    checks: list[ConstraintCheckItem] = []

    for entry in result.execution_log:
        if entry.get("status") != "executed":
            continue
        exec_date = pd.Timestamp(entry["execution_date"])
        decision_date = pd.Timestamp(entry["decision_date"])
        weights_day = {
            str(col): float(val)
            for col, val in result.weights.loc[exec_date].items()
            if pd.notna(val) and float(val) > 0
        }
        violations = _check_weights(weights_day, classifications, constraints)
        if constraints.turnover_limit is not None:
            actual_turnover = float(result.turnover.loc[exec_date])
            if actual_turnover > float(constraints.turnover_limit):
                violations.append(
                    ConstraintViolationItem(
                        constraint="turnover_limit",
                        message=(
                            f"换手 {actual_turnover:.4f} 超过 turnover_limit="
                            f"{float(constraints.turnover_limit):.4f}。"
                        ),
                        severity="error",
                        target=str(exec_date.date()),
                        actual=actual_turnover,
                        limit=float(constraints.turnover_limit),
                        unit="turnover",
                    )
                )
        checks.append(
            ConstraintCheckItem(
                scope="history",
                decision_date=str(decision_date.date()),
                execution_date=str(exec_date.date()),
                status="checked",
                violations=violations,
                unsupported_constraints=[],
                not_evaluated_constraints=[],
            )
        )

    if latest_weights:
        latest_violations = _check_weights(
            {k: float(v) for k, v in latest_weights.items() if v > 0},
            classifications,
            constraints,
        )
        not_evaluated = (
            ["turnover_limit"] if constraints.turnover_limit is not None else []
        )
        checks.append(
            ConstraintCheckItem(
                scope="latest",
                decision_date=latest_data_date,
                execution_date=None,
                status="checked",
                violations=latest_violations,
                unsupported_constraints=[],
                not_evaluated_constraints=not_evaluated,
            )
        )
    else:
        checks.append(
            ConstraintCheckItem(
                scope="latest",
                decision_date=latest_data_date,
                execution_date=None,
                status="not_evaluated",
                violations=[],
                unsupported_constraints=[],
                not_evaluated_constraints=["latest_recommendation"],
            )
        )
    return checks


def _json_safe(value: Any) -> Any:
    """递归转换：非有限浮点 → None；numpy 标量 → Python 标量；日期 → ISO 串。

    JSON 不允许写 NaN/Infinity；该函数保证 result_summary 严格可序列化。
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, pd.Timestamp):
        return str(value)
    if isinstance(value, pd.Series):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if np.isfinite(f) else None
    if hasattr(value, "isoformat"):  # datetime.date / datetime.datetime
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, pd.Index, np.ndarray)):
        return [_json_safe(v) for v in list(value)]
    return value


# ── Result conversion ─────────────────────────────────────────────────

RESULT_SCHEMA_VERSION = "2"
FRAMEWORK_VERSION = "1"


def _compute_metrics(result: BacktestResult) -> StrategyMetrics:
    """schema 映射：指标数值全部来自统一框架 calculate_metrics，不本地重算。"""
    m = calculate_metrics(result)
    return StrategyMetrics(
        total_return=float(m["total_return"]),
        annual_return=float(m["annual_return"]),
        annual_volatility=float(m["annual_volatility"]),
        sharpe=float(m["sharpe"]),
        max_drawdown=float(m["max_drawdown"]),
        win_rate=float(m["win_rate"]),
        sortino=float(m["sortino"]),
        calmar=float(m["calmar"]),
        turnover_sum=float(m["turnover_sum"]),
        cost_sum=float(m["cost_sum"]),
        fee_amount_sum=float(m["fee_amount_sum"]),
        rebalance_count=float(m["rebalance_count"]),
    )


def _result_to_dict(
    *,
    result: BacktestResult,
    run_context: RunContext,
    constraints: CoreOptimizationConstraints,
    latest_weights: dict[str, float] | None = None,
    latest_data_date: str | None = None,
    latest_reason: str | None = None,
) -> dict[str, Any]:
    """Serialize a BacktestResult into the schema_version='2' JSON-safe dict.

    新结果写入前用 RunDiagnostics 嵌套 schema 严格校验诊断部分；历史明细
    （decision_log / execution_log / run_context）只读快照，不引用最新数据。
    """
    metrics = _compute_metrics(result)

    constraint_checks = _build_constraint_checks(
        result,
        run_context.classifications,
        constraints,
        latest_weights or {},
        latest_data_date,
        latest_reason,
    )

    decision_log_items = [
        DecisionLogItem(
            decision_date=str(pd.Timestamp(e["decision_date"]).date()),
            eligible_count=int(e["eligible_count"]),
            top_n=int(e["top_n"]),
            status=str(e["status"]),
            exclusions=[
                {
                    "sec": str(ex.get("sec", "")),
                    "category": ex.get("category"),
                    "factor": ex.get("factor"),
                    "instance_index": ex.get("instance_index"),
                    "params": ex.get("params"),
                    "reason": str(ex.get("reason", "")),
                }
                for ex in e.get("exclusions", [])
            ],
        )
        for e in result.decision_log
    ]
    execution_log_items = [
        ExecutionLogItem(
            decision_date=str(pd.Timestamp(e["decision_date"]).date()),
            execution_date=(
                str(pd.Timestamp(e["execution_date"]).date())
                if e.get("execution_date") is not None
                else None
            ),
            status=str(e["status"]),
            reason=e.get("reason"),
            missing_codes=[str(c) for c in e.get("missing_codes", [])],
        )
        for e in result.execution_log
    ]

    diagnostics = RunDiagnostics(
        schema_version=RESULT_SCHEMA_VERSION,
        framework_version=FRAMEWORK_VERSION,
        execution_config=dict(result.config_snapshot.get("execution", {})),
        metrics_config={"annualization": 252, "risk_free_rate": 0.01},
        run_context=run_context,
        decision_log=decision_log_items,
        execution_log=execution_log_items,
        constraint_checks=constraint_checks,
    )

    # 兼容字段：constraint_violations = 最新组合（latest scope）的违反项。
    latest_check = next((c for c in constraint_checks if c.scope == "latest"), None)
    legacy_violations = latest_check.violations if latest_check is not None else []

    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "framework_version": FRAMEWORK_VERSION,
        "execution_config": diagnostics.execution_config,
        "metrics_config": diagnostics.metrics_config,
        "run_context": diagnostics.run_context.model_dump(),
        "decision_log": [item.model_dump() for item in decision_log_items],
        "execution_log": [item.model_dump() for item in execution_log_items],
        "constraint_checks": [item.model_dump() for item in constraint_checks],
        "metrics": metrics.model_dump(),
        "constraint_violations": [item.model_dump() for item in legacy_violations],
        "equity_curve": {
            str(k.date()): float(v) for k, v in result.equity_curve.dropna().items()
        },
        "daily_returns": {
            str(k.date()): float(v) for k, v in result.daily_returns.dropna().items()
        },
        # weights 为实际日末持仓权重（不再是目标权重；目标见 decision_log/框架）。
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
        "latest_recommendation_reason": latest_reason,
    }
    safe = _json_safe(payload)
    # 嵌套 schema 严格校验（写入前）：校验失败直接抛错，不写入半成品。
    RunDiagnostics.model_validate(
        {
            "schema_version": safe["schema_version"],
            "framework_version": safe["framework_version"],
            "execution_config": safe["execution_config"],
            "metrics_config": safe["metrics_config"],
            "run_context": safe["run_context"],
            "decision_log": safe["decision_log"],
            "execution_log": safe["execution_log"],
            "constraint_checks": safe["constraint_checks"],
        }
    )
    return safe


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