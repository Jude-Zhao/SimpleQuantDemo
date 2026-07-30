"""Tests for factor API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_get_factors():
    response = client.get("/api/factors")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    names = [f["name"] for f in data]
    assert "momentum" in names
    assert "volatility" in names
    assert "reversal" in names
    # Check meta fields
    f = next(f for f in data if f["name"] == "momentum")
    assert f["display_name"] == "动量因子"
    assert f["category"] == "动量"
    assert "params_schema" in f
    assert "window" in f["params_schema"]


def test_compute_factor_momentum():
    response = client.post("/api/factors/compute", json={
        "factor_name": "momentum",
        "params": {"window": 5},
        "horizon": 5,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["factor_name"] == "momentum"
    assert "ic_result" in data
    assert "ic_mean" in data["ic_result"]
    assert "ic_series" in data["ic_result"]
    assert len(data["ic_result"]["ic_series"]) > 0
    assert "group_returns" in data
    assert len(data["group_returns"]) == 5


def test_compute_factor_unknown():
    response = client.post("/api/factors/compute", json={
        "factor_name": "nonexistent",
        "params": {},
    })
    assert response.status_code == 400
