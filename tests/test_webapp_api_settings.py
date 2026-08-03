"""Tests for settings API endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_get_settings():
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "datasource" in data
    assert "system" in data
    assert "primary" in data["datasource"]
    assert "secondary" in data["datasource"]
    assert "cache_enabled" in data["datasource"]
    assert "cache_days_daily" in data["datasource"]
    assert "cache_days_minute" in data["datasource"]
    assert "version" in data["system"]
    assert "database_connected" in data["system"]