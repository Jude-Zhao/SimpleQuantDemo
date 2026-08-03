"""Tests for factor correlation API endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_factor_correlation_valid():
    response = client.post("/api/factors/correlation", json={
        "factor_names": ["momentum", "volatility", "reversal"],
    })
    assert response.status_code == 200
    data = response.json()
    assert "factor_names" in data
    assert "correlation_matrix" in data
    assert data["factor_names"] == ["momentum", "volatility", "reversal"]
    assert len(data["correlation_matrix"]) == 3
    # Diagonal should be 1.0
    for i in range(3):
        assert abs(data["correlation_matrix"][i][i] - 1.0) < 1e-6


def test_factor_correlation_unknown_factor():
    response = client.post("/api/factors/correlation", json={
        "factor_names": ["does_not_exist"],
    })
    assert response.status_code == 400


def test_factor_correlation_empty():
    response = client.post("/api/factors/correlation", json={"factor_names": []})
    assert response.status_code == 200
    data = response.json()
    assert data["factor_names"] == []
    assert data["correlation_matrix"] == []