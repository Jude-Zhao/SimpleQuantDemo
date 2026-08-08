"""Tests for market API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def test_list_etfs():
    response = client.get("/api/market/etf/list")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    codes = [e["sec_code"] for e in data]
    assert "510300.SH" in codes
    assert "510500.SH" in codes
    # Check required fields
    for e in data:
        assert "sec_code" in e
        assert "sec_name" in e
        assert "category" in e


def test_list_etfs_has_expected_categories():
    response = client.get("/api/market/etf/list")
    data = response.json()
    categories = set(e["category"] for e in data)
    assert "宽基" in categories
    assert "商品" in categories
    assert "跨境" in categories
