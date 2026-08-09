"""Tests for strategy API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.models.database import SessionLocal
from webapp.models.strategy_run import StrategyRun

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