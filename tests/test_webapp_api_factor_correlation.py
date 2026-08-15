"""Tests for factor correlation API endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_factor_correlation_instance():
    response = client.post("/api/factors/correlation", json={
        "granularity": "instance",
        "factor_ids": ["aroon_diff", "low_vol_60", "ma60_slope_reversal"],
    })
    assert response.status_code == 200
    data = response.json()
    assert "granularity" in data
    assert data["granularity"] == "instance"
    assert "labels" in data
    assert "correlation_matrix" in data
    assert data["labels"] == ["aroon_diff", "low_vol_60", "ma60_slope_reversal"]
    assert len(data["correlation_matrix"]) == 3
    # Diagonal should be 1.0
    for i in range(3):
        assert abs(data["correlation_matrix"][i][i] - 1.0) < 1e-6


def test_factor_correlation_class():
    response = client.post("/api/factors/correlation", json={
        "granularity": "class",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["granularity"] == "class"
    assert "volume" in data["labels"]  # now holds money_flow_20
    assert "momentum" in data["labels"]
    assert "volatility" in data["labels"]
    assert "reversal" in data["labels"]
    n = len(data["labels"])
    assert len(data["correlation_matrix"]) == n
    assert n >= 1


def test_factor_correlation_unknown_factor():
    response = client.post("/api/factors/correlation", json={
        "granularity": "instance",
        "factor_ids": ["does_not_exist"],
    })
    assert response.status_code == 400


def test_factor_correlation_empty():
    response = client.post("/api/factors/correlation", json={
        "granularity": "instance",
        "factor_ids": [],
    })
    assert response.status_code == 200
    data = response.json()
    assert data["labels"] == []
    assert data["correlation_matrix"] == []