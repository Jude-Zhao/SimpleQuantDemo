"""Tests for dashboard API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_dashboard_stats():
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "universe_count" in data
    assert "factor_count" in data
    assert "run_count_today" in data
    assert "system_status" in data
    assert data["system_status"] == "ok"
    # factor_count counts factor instances from factors.yaml (>= 3 non-empty classes)
    assert data["factor_count"] >= 3


def test_dashboard_recent_runs():
    response = client.get("/api/dashboard/recent-runs?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_dashboard_factor_ranking():
    response = client.get("/api/dashboard/factor-ranking")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    keys = set()
    for item in data:
        assert "key" in item
        assert "display_name" in item
        assert "is_empty" in item
        assert "class_rank_ic_mean" in item
        assert "class_rank_icir" in item
        assert "factors" in item
        assert isinstance(item["factors"], list)
        keys.add(item["key"])
        for f in item["factors"]:
            assert "name" in f
            assert "params" in f
            assert "rank_ic_mean" in f
            assert "rank_icir" in f
    # factor categories come from factors.yaml, so keys must be unique.
    assert len(keys) == len(data)


def test_dashboard_returns_ranking():
    response = client.get("/api/dashboard/returns-ranking?days=20")
    assert response.status_code == 200
    data = response.json()
    assert "days" in data
    assert "as_of" in data
    assert "momentum" in data
    assert "reversal" in data
    assert data["days"] == 20