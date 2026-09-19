"""Tests for strategy API endpoints."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.models.database import SessionLocal
from webapp.models.strategy_run import StrategyRun
from webapp.schemas.strategy import StrategyRunRequest

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

client = TestClient(app)


def test_list_strategies():
    response = client.get("/api/strategies")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    names = [s["name"] for s in data]
    assert "faa" in names
    assert "eaa" in names
    # Each strategy should have param schema
    for s in data:
        assert "params_schema" in s
        assert isinstance(s["params_schema"], list)


def test_get_runs_empty_initial():
    response = client.get("/api/strategies/runs")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_run_unknown_strategy_fails_gracefully():
    response = client.post("/api/strategies/run", json={
        "strategy_type": "unknown_strategy",
        "params": {},
    })
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert "未知策略" in data["error_msg"]


def test_get_run_detail_not_found():
    response = client.get("/api/strategies/runs/999999")
    assert response.status_code == 404


def test_export_run_not_found():
    response = client.get("/api/strategies/runs/999999/export")
    assert response.status_code == 404


def test_concurrent_run_rejected():
    """A second submission is rejected while one run is pending/running."""
    db = SessionLocal()
    try:
        db.add(StrategyRun(strategy_type="faa", params={}, status="running"))
        db.commit()
    finally:
        db.close()

    try:
        response = client.post("/api/strategies/run", json={
            "strategy_type": "faa",
            "params": {"top_n": 5},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "运行中" in data["error_msg"]
    finally:
        db = SessionLocal()
        try:
            db.query(StrategyRun).filter(StrategyRun.status == "running").delete()
            db.commit()
        finally:
            db.close()


# ── F11: 提交层参数校验（服务端契约，非法值 422）──────────────────────


@pytest.mark.parametrize(
    "params",
    [
        {"exponents": {"momentum": 1.0}, "beta": 0},        # β=0 激活缺失格
        {"exponents": {"momentum": 1.0}, "beta": -1.5},     # 负 β 颠倒排序
        {"exponents": {"momentum": 1.0}, "beta": "abc"},    # 非数值
        {"top_n": 0},                                        # 非正
        {"top_n": 5.5},                                      # 非整数
        {"top_n": True},                                     # bool 伪装
        {"rebalance_freq": "daily"},                         # 非法频率
        {"class_weights": {"momentum": -0.5}},               # 负权重
        {"class_weights": {"momentum": 0}},                  # 非空但无正项
        {"exponents": {"momentum": "x"}},                    # 非数值
    ],
)
def test_run_rejects_invalid_params(params):
    response = client.post("/api/strategies/run", json={
        "strategy_type": "faa",
        "params": params,
    })
    assert response.status_code == 422
    # 校验在 schema 层完成：不创建任何运行记录
    db = SessionLocal()
    try:
        assert db.query(StrategyRun).count() == 0
    finally:
        db.close()


def test_run_accepts_valid_params_schema():
    """合法参数通过 schema 校验（直接构造，不启动任务）。"""
    req = StrategyRunRequest(strategy_type="eaa", params={
        "top_n": 5,
        "rebalance_freq": "5d",
        "exponents": {"momentum": 1.0, "reversal": 0.0},  # 0 = 显式关闭类别
        "beta": 1.2,
    })
    assert req.params["beta"] == 1.2
    # 缺省 params 合法（默认值合并属 F12 范畴）
    req = StrategyRunRequest(strategy_type="faa")
    assert req.params == {}