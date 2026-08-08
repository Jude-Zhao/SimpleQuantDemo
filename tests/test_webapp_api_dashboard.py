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
    assert data["factor_count"] >= 3


def test_dashboard_etf_price_missing_codes():
    response = client.get("/api/dashboard/etf-price")
    assert response.status_code == 422  # requires codes


def test_dashboard_etf_price_with_codes():
    response = client.get("/api/dashboard/etf-price?codes=510300.SH,510500.SH")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # May be empty if no cached data, but must not error
    for item in data:
        assert "date" in item
        assert "sec_code" in item
        assert "close" in item


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
    for item in data:
        assert "name" in item
        assert "rank_ic_mean" in item
        assert "rank_icir" in item


def test_dashboard_returns_ranking():
    response = client.get("/api/dashboard/returns-ranking?days=20")
    assert response.status_code == 200
    data = response.json()
    assert "days" in data
    assert "as_of" in data
    assert "momentum" in data
    assert "reversal" in data
    assert data["days"] == 20