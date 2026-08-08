"""Tests for universe API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_get_universe_empty():
    response = client.get("/api/universe")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_add_to_universe():
    response = client.post("/api/universe", json={
        "items": [
            {"sec_code": "510300.SH", "sec_name": "沪深300ETF", "meta": {"category": "宽基"}},
            {"sec_code": "510500.SH", "sec_name": "中证500ETF", "meta": {"category": "宽基"}},
        ]
    })
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    codes = [item["sec_code"] for item in data]
    assert "510300.SH" in codes
    assert "510500.SH" in codes
    assert data[0]["is_active"] is True


def test_get_universe_after_add():
    # First add some items
    client.post("/api/universe", json={
        "items": [
            {"sec_code": "510300.SH", "sec_name": "沪深300ETF"},
            {"sec_code": "159915.SZ", "sec_name": "创业板ETF"},
        ]
    })

    response = client.get("/api/universe")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    codes = [item["sec_code"] for item in data]
    assert "510300.SH" in codes


def test_remove_from_universe():
    # Add first
    client.post("/api/universe", json={
        "items": [
            {"sec_code": "518880.SH", "sec_name": "黄金ETF"},
        ]
    })

    # Remove
    response = client.delete("/api/universe/518880.SH")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify removed
    response = client.get("/api/universe")
    codes = [item["sec_code"] for item in response.json()]
    assert "518880.SH" not in codes


def test_remove_nonexistent():
    response = client.delete("/api/universe/999999.SH")
    assert response.status_code == 404


def test_add_empty_batch():
    response = client.post("/api/universe", json={"items": []})
    assert response.status_code == 400
