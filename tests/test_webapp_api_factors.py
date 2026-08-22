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
    keys = {c["key"] for c in data}
    assert "momentum" in keys
    assert "volatility" in keys
    assert "volume" in keys  # empty class present
    # Every class exposes the grouped structure
    for cat in data:
        assert "key" in cat
        assert "display_name" in cat
        assert "is_empty" in cat
        assert isinstance(cat["factors"], list)
        for f in cat["factors"]:
            assert "id" in f
            assert "name" in f
            assert "params" in f
            assert "display_name" in f
            assert "params_schema" in f
    # Non-empty momentum class has the migrated macd_hist
    mom = next(c for c in data if c["key"] == "momentum")
    assert mom["is_empty"] is False
    assert "macd_hist" in [f["id"] for f in mom["factors"]]
    # volume now holds migrated mfi / psy20
    vol = next(c for c in data if c["key"] == "volume")
    assert vol["is_empty"] is False
    assert "mfi" in [f["id"] for f in vol["factors"]]


def test_compute_factor_unknown():
    """Unknown factor should return 400 or 503 (if no data available)."""
    response = client.post("/api/factors/compute", json={
        "factor_name": "nonexistent",
        "params": {},
    })
    assert response.status_code in (400, 503)
