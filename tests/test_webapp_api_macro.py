"""Tests for macro API endpoints (uses FastAPI TestClient)."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from webapp.main import app

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

client = TestClient(app)


def test_macro_fields_daily():
    resp = client.get("/api/macro/fields?frequency=daily")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 10
    names = {f["name"] for f in data}
    assert "shibor_3m" in names
    assert "cn_gov_10y" in names
    assert "spx" in names


def test_macro_fields_monthly():
    resp = client.get("/api/macro/fields?frequency=monthly")
    assert resp.status_code == 200
    data = resp.json()
    names = {f["name"] for f in data}
    assert {"m1_yoy", "m2_yoy", "cpi_yoy"}.issubset(names)


def test_macro_daily_empty_query():
    # Query a range far in the future -> empty
    resp = client.get("/api/macro/daily?start_date=2030-01-01&end_date=2030-01-31")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dates"] == []


def test_macro_monthly_empty_query():
    resp = client.get("/api/macro/monthly?start_month=2030-01&end_month=2030-06")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dates"] == []


def test_macro_sync_status_404():
    resp = client.get("/api/macro/sync/nonexistent")
    assert resp.status_code == 404


def test_market_sync_status_404():
    resp = client.get("/api/market/sync/nonexistent")
    assert resp.status_code == 404
