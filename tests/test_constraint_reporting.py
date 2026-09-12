"""B7 / OPT-04：约束检查报告测试。

- 历史逐执行日检查与 latest 独立；
- 注入 turnover=0.40、limit=0.30：actual 必须是 0.40（结构化字段，不从 message 解析）；
- latest 换手无法评价 → 明确记录 not_evaluated；
- 检查前后持仓、交易和净值深比较不变（检查是纯读取）；
- 空配置 GET 与运行使用同一默认（single_max_weight=0.15）。
"""

from __future__ import annotations

import copy
import dataclasses

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.backtest import ExecutionConfig
from core.backtest.engine import execute_backtest
from core.optimization import OptimizationConstraints as CoreOptimizationConstraints
from webapp.main import app
from webapp.services import constraint_service
from webapp.services.strategy_service import (
    _build_constraint_checks,
    _result_to_dict,
    load_core_constraints,
)

client = TestClient(app)

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

DATES = pd.to_datetime(
    ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]
)


def _close() -> pd.DataFrame:
    return pd.DataFrame(
        {"A": [100.0] * 6, "B": [100.0] * 6}, index=DATES
    )


def _targets() -> pd.DataFrame:
    rows = {DATES[0]: {"A": 0.6, "B": 0.4}, DATES[2]: {"A": 1.0, "B": 0.0}}
    idx = pd.DatetimeIndex(list(rows), name="date")
    mat = [list(rows[d].values()) for d in idx]
    return pd.DataFrame(mat, index=idx, columns=["A", "B"])


def _build_result() -> "object":
    """两次实际执行：第一次换手 0.40（全仓换）、第二次 0.05（按金额近似）。"""
    engine_result = execute_backtest(
        _close(), _targets(), ExecutionConfig(initial_cash=1.0, transaction_cost_bps=0.5)
    )
    # 注入历史换手 0.40/0.05（覆盖框架计算的 turnover 数值，保持只读结构）
    turnover = engine_result.turnover.copy()
    turnover.loc[DATES[1]] = 0.40
    turnover.loc[DATES[3]] = 0.05
    return dataclasses.replace(engine_result, turnover=turnover)


def test_history_and_latest_checks_are_separate() -> None:
    result = _build_result()
    classifications = {"A": {"category": "broad"}, "B": {"category": "broad"}}
    constraints = CoreOptimizationConstraints(
        single_max_weight=0.5, turnover_limit=0.30
    )

    checks = _build_constraint_checks(
        result, classifications, constraints,
        latest_weights={"A": 0.5, "B": 0.5},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )

    history = [c for c in checks if c.scope == "history"]
    latest = [c for c in checks if c.scope == "latest"]
    assert len(history) == 2  # 两次实际执行
    assert len(latest) == 1

    # 注入 turnover=0.40、limit=0.30 → actual 必须是 0.40（结构化字段）
    first = history[0]
    turnover_violations = [v for v in first.violations if v.constraint == "turnover_limit"]
    assert len(turnover_violations) == 1
    tv = turnover_violations[0]
    assert tv.actual == pytest.approx(0.40)
    assert tv.limit == pytest.approx(0.30)
    assert tv.unit == "turnover"

    # 第二次执行换手 0.05 < 0.30 → 无换手违反
    second = history[1]
    assert all(v.constraint != "turnover_limit" for v in second.violations)

    # latest：换手无法评价（没有确定未来成交价）→ 明确记录，不用目标权重差冒充
    lv = latest[0]
    assert lv.scope == "latest"
    assert lv.execution_date is None
    assert "turnover_limit" in lv.not_evaluated_constraints
    assert all(v.constraint != "turnover_limit" for v in lv.violations)


def test_single_weight_violation_carries_structured_fields() -> None:
    result = _build_result()
    classifications = {"A": {"category": "broad"}, "B": {"category": "broad"}}
    constraints = CoreOptimizationConstraints(single_max_weight=0.05)

    checks = _build_constraint_checks(
        result, classifications, constraints,
        latest_weights={"A": 0.6},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )
    latest = [c for c in checks if c.scope == "latest"][0]
    single = [v for v in latest.violations if v.constraint.startswith("single_max_weight")]
    assert len(single) == 1
    sv = single[0]
    assert sv.actual == pytest.approx(0.6)
    assert sv.limit == pytest.approx(0.05)
    assert sv.unit == "weight"
    assert sv.target == "A"


def test_checks_do_not_change_result_state() -> None:
    """检查前后持仓、交易和净值深比较不变。"""
    result = _build_result()
    classifications = {"A": {"category": "broad"}, "B": {"category": "broad"}}
    constraints = CoreOptimizationConstraints(single_max_weight=0.15, turnover_limit=0.30)

    before = {
        "weights": copy.deepcopy(result.weights),
        "trades": copy.deepcopy(result.trades),
        "equity": copy.deepcopy(result.equity_curve),
        "holdings": copy.deepcopy(result.holdings),
        "cash": copy.deepcopy(result.cash),
    }
    _build_constraint_checks(
        result, classifications, constraints,
        latest_weights={"A": 0.5, "B": 0.5},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )
    assert result.weights.equals(before["weights"])
    assert result.trades.equals(before["trades"])
    assert result.equity_curve.equals(before["equity"])
    assert result.holdings.equals(before["holdings"])
    assert result.cash.equals(before["cash"])


def test_empty_config_get_and_run_share_default() -> None:
    """空配置：GET /api/constraints 与运行（load_core_constraints）用同一默认 0.15。"""
    from webapp.models.database import SessionLocal

    db = SessionLocal()
    try:
        web = constraint_service.load_constraints(db)
        core = load_core_constraints(db)
    finally:
        db.close()
    assert web.single_max_weight == pytest.approx(0.15)
    assert core.single_max_weight == pytest.approx(0.15)
    # GET 端点同样返回该默认
    resp = client.get("/api/constraints")
    assert resp.status_code == 200
    assert resp.json()["single_max_weight"] == pytest.approx(0.15)


def test_result_to_dict_constraint_checks_shape() -> None:
    """序列化后的 constraint_checks 契约完整（scope/status/violations 字段）。"""
    result = _build_result()
    run_context = type(
        "Ctx",
        (),
        {
            "strategy_type": "faa",
            "params": {},
            "sec_names": {"A": "甲", "B": "乙"},
            "factor_categories": [],
            "classifications": {"A": {"category": "broad"}, "B": {"category": "broad"}},
            "constraints": {"single_max_weight": 0.15, "turnover_limit": 0.30},
        },
    )()
    from webapp.schemas.strategy import RunContext as RunContextSchema

    ctx = RunContextSchema(
        strategy_type="faa",
        params={},
        sec_names={"A": "甲", "B": "乙"},
        factor_categories=[],
        classifications={"A": {"category": "broad"}, "B": {"category": "broad"}},
        constraints={"single_max_weight": 0.15, "turnover_limit": 0.30},
    )
    payload = _result_to_dict(
        result=result,
        run_context=ctx,
        constraints=CoreOptimizationConstraints(single_max_weight=0.15, turnover_limit=0.30),
        latest_weights={"A": 0.5, "B": 0.5},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )
    checks = payload["constraint_checks"]
    assert all(
        {"scope", "decision_date", "execution_date", "status", "violations",
         "unsupported_constraints", "not_evaluated_constraints"}.issubset(c)
        for c in checks
    )
    # 换手违反项以结构化字段出现在 JSON 中
    tv = [
        v
        for c in checks
        for v in c["violations"]
        if v["constraint"] == "turnover_limit"
    ]
    assert len(tv) == 1
    assert tv[0]["actual"] == pytest.approx(0.40)
    assert tv[0]["limit"] == pytest.approx(0.30)
    assert tv[0]["unit"] == "turnover"
