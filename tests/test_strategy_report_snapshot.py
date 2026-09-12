"""B7 / OPT-03：Web 历史报告快照测试。

三个计划决策日场景：正常且有排除、资格不足跳过、正常生成但成交取消。
同名不同参数无效因子完整保留；修改数据/配置且数据获取替身调用即失败后，
GET 详情与导出仍读相同快照（深比较一致），Web 不用最新数据重算历史明细。
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.backtest import ExecutionConfig
from core.backtest.engine import execute_backtest
from core.optimization import OptimizationConstraints as CoreOptimizationConstraints
from webapp.main import app
from webapp.models.database import SessionLocal
from webapp.models.strategy_run import StrategyRun
from webapp.schemas.strategy import RunContext
from webapp.services import data_service as data_service_module
from webapp.services import strategy_service

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

client = TestClient(app)

DATES = pd.to_datetime(
    ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]
)
SECS = ["A", "B", "C"]


def _close() -> pd.DataFrame:
    # d5（2026-01-12）B 缺价 → 第二次调仓（d4 决策 → d5 执行）被取消
    data = {
        "A": [100.0] * 6,
        "B": [100.0, 100.0, 100.0, 100.0, 100.0, np.nan],
        "C": [100.0] * 6,
    }
    return pd.DataFrame(data, index=DATES)


def _targets() -> pd.DataFrame:
    rows = {
        DATES[0]: {"A": 0.6, "B": 0.4, "C": 0.0},   # 决策1：正常生成（执行于 d1）
        DATES[4]: {"A": 0.0, "B": 1.0, "C": 0.0},   # 决策3：正常生成（执行日 d5 缺价 → 取消）
    }
    idx = pd.DatetimeIndex(list(rows), name="date")
    mat = np.zeros((len(idx), len(SECS)))
    for i, d in enumerate(idx):
        for j, c in enumerate(SECS):
            mat[i, j] = rows[d][c]
    return pd.DataFrame(mat, index=idx, columns=SECS)


def _decision_log() -> list[dict]:
    """三个计划决策日：正常且有排除（同名不同参数）、资格不足、正常但取消。"""
    return [
        {
            "decision_date": DATES[0],
            "eligible_count": 3,
            "top_n": 2,
            "status": "target_created",
            "exclusions": [
                # 同名不同参数两实例：全部保留，不只保留其一
                {
                    "sec": "C",
                    "category": "volume",
                    "factor": "mfi",
                    "instance_index": 0,
                    "params": {"window": 12},
                    "reason": "missing",
                },
                {
                    "sec": "C",
                    "category": "volume",
                    "factor": "mfi",
                    "instance_index": 1,
                    "params": {"window": 20},
                    "reason": "non_finite",
                },
            ],
        },
        {
            "decision_date": DATES[2],
            "eligible_count": 1,
            "top_n": 2,
            "status": "skipped_insufficient",
            "exclusions": [
                {"sec": "B", "category": "momentum", "factor": "macd_hist",
                 "instance_index": 0, "params": {"fast": 12, "slow": 26}, "reason": "missing"},
                {"sec": "C", "category": "momentum", "factor": "macd_hist",
                 "instance_index": 0, "params": {"fast": 12, "slow": 26}, "reason": "missing"},
            ],
        },
        {
            "decision_date": DATES[4],
            "eligible_count": 2,
            "top_n": 2,
            "status": "target_created",
            "exclusions": [],
        },
    ]


def _build_result():
    engine_result = execute_backtest(
        _close(), _targets(), ExecutionConfig(initial_cash=1.0, transaction_cost_bps=0.5)
    )
    result = dataclasses.replace(
        engine_result,
        decision_log=_decision_log(),
        rebalance_dates=pd.DatetimeIndex([DATES[0], DATES[2], DATES[4]]),
    )
    return result


def _run_context() -> RunContext:
    return RunContext(
        strategy_type="faa",
        params={"top_n": 2, "rebalance_freq": "5d",
                "class_weights": {"momentum": 0.2, "reversal": 0.3, "volatility": 0.25, "volume": 0.25}},
        sec_names={"A": "甲ETF", "B": "乙ETF", "C": "丙ETF"},
        factor_categories=[
            {"key": "momentum", "display_name": "动量", "is_empty": False,
             "factors": [{"name": "macd_hist", "params": {"fast": 12, "slow": 26, "signal": 9}}]},
            {"key": "volume", "display_name": "量能", "is_empty": False,
             "factors": [{"name": "mfi", "params": {"window": 12}}, {"name": "mfi", "params": {"window": 20}}]},
        ],
        classifications={"A": {"category": "broad"}, "B": {"category": "industry"}, "C": {"category": "industry"}},
        constraints={"single_min_weight": None, "single_max_weight": 0.15,
                     "category_constraints": [], "turnover_limit": None},
    )


def _persist_run(payload: dict) -> int:
    db = SessionLocal()
    try:
        run = StrategyRun(
            strategy_type="faa",
            params={"top_n": 2, "rebalance_freq": "5d"},
            universe_snapshot=SECS,
            start_date="2026-01-05",
            end_date="2026-01-12",
            result_summary=payload,
            status="success",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def _mutate_latest_state() -> None:
    """修改标的名与约束配置：历史明细必须不受影响。"""
    db = SessionLocal()
    try:
        from webapp.models.constraint_config import ConstraintConfig
        from webapp.models.universe import UniverseItem

        for item in db.query(UniverseItem).all():
            item.sec_name = f"改名-{item.sec_code}"
        row = db.query(ConstraintConfig).order_by(ConstraintConfig.id).first()
        if row is None:
            db.add(ConstraintConfig(config={"single_max_weight": 0.9}))
        else:
            row.config = {"single_max_weight": 0.9}
        db.commit()
    finally:
        db.close()


def _install_failing_stubs(monkeypatch) -> None:
    """所有数据获取替身：调用即失败（证明 GET 不做任何重算/补数据）。"""

    def _fail(*args, **kwargs):  # pragma: no cover - 调用即测试失败
        raise AssertionError("历史报告读取不得触发数据获取")

    monkeypatch.setattr(data_service_module, "get_etf_price", _fail)
    monkeypatch.setattr(data_service_module, "get_etf_list", _fail)
    monkeypatch.setattr(strategy_service, "classify_universe", _fail)
    monkeypatch.setattr(strategy_service, "get_etf_price", _fail)


def test_snapshot_persists_diagnostics_and_is_immutable(monkeypatch) -> None:
    result = _build_result()
    payload = strategy_service._result_to_dict(
        result=result,
        run_context=_run_context(),
        constraints=CoreOptimizationConstraints(single_max_weight=0.15),
        latest_weights={"A": 0.6, "B": 0.4},
        latest_data_date=str(DATES[-1].date()),
        latest_reason=None,
    )

    # schema 契约
    assert payload["schema_version"] == "2"
    assert payload["framework_version"] == "1"
    assert payload["execution_config"] == {"initial_cash": 1.0, "transaction_cost_bps": 0.5}
    assert payload["metrics_config"]["risk_free_rate"] == 0.01
    assert len(payload["decision_log"]) == 3
    statuses = [d["status"] for d in payload["decision_log"]]
    assert statuses == ["target_created", "skipped_insufficient", "target_created"]
    # 同名不同参数无效因子完整保留
    exclusions0 = payload["decision_log"][0]["exclusions"]
    mfi = [e for e in exclusions0 if e["factor"] == "mfi"]
    assert sorted(e["instance_index"] for e in mfi) == [0, 1]
    assert sorted(e["params"]["window"] for e in mfi) == [12, 20]
    # 组合生成与成交取消区分：决策3生成组合，执行记录为取消且不误报已成交
    assert payload["decision_log"][2]["status"] == "target_created"
    cancelled = [e for e in payload["execution_log"] if e["status"] == "cancelled_missing_price"]
    assert len(cancelled) == 1
    assert cancelled[0]["decision_date"] == str(DATES[4].date())
    assert cancelled[0]["missing_codes"] == ["B"]
    executed = [e for e in payload["execution_log"] if e["status"] == "executed"]
    assert [e["decision_date"] for e in executed] == [str(DATES[0].date())]

    run_id = _persist_run(payload)

    # 首次 GET：快照原样返回
    resp = client.get(f"/api/strategies/runs/{run_id}")
    assert resp.status_code == 200
    before = resp.json()["result_summary"]
    assert before == payload

    # 修改标的名/因子参数/约束配置 + 数据获取替身调用即失败
    _mutate_latest_state()
    _install_failing_stubs(monkeypatch)

    resp2 = client.get(f"/api/strategies/runs/{run_id}")
    assert resp2.status_code == 200
    after = resp2.json()["result_summary"]
    assert after == before, "修改最新数据/配置后，历史报告必须保持原快照"

    # 导出同样只读快照
    resp_export = client.get(f"/api/strategies/runs/{run_id}/export")
    assert resp_export.status_code == 200
    csv_text = resp_export.json()["csv"]
    lines = [ln for ln in csv_text.strip().splitlines() if ln]
    assert lines[0] == "date,nav"
    nav_rows = [ln.split(",") for ln in lines[1:]]
    assert len(nav_rows) == len(payload["equity_curve"])
    for date, nav in nav_rows:
        assert payload["equity_curve"][date] == pytest.approx(float(nav))

    # dashboard 最近运行只读 metrics.total_return（B7 已删除 equity_curve 兜底）
    resp_recent = client.get("/api/dashboard/recent-runs")
    assert resp_recent.status_code == 200
    recent = next(x for x in resp_recent.json() if x["id"] == run_id)
    assert recent["total_return"] == payload["metrics"]["total_return"]


def test_json_payload_has_no_nan_or_infinity() -> None:
    """JSON 只保存有限数值或 null，不写 NaN/Infinity。"""
    result = _build_result()
    payload = strategy_service._result_to_dict(
        result=result,
        run_context=_run_context(),
        constraints=CoreOptimizationConstraints(single_max_weight=0.15),
        latest_weights={},
        latest_data_date=str(DATES[-1].date()),
        latest_reason="最新因子日合格证券不足",
    )
    import json as _json

    text = _json.dumps(payload, allow_nan=False)  # allow_nan=False → NaN/Infinity 抛错
    assert "NaN" not in text and "Infinity" not in text
    # latest 推荐不足：空持仓 + 明确理由，不回填旧推荐
    assert payload["latest_weights"] == {}
    assert payload["latest_recommendation_reason"] == "最新因子日合格证券不足"
