"""Tests for universe API endpoints."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from webapp.main import app

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

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


# ── BUG-17：sec_code 白名单校验（六位数字 + .SH/.SZ，整批拒绝）───────────

def test_add_universe_rejects_invalid_sec_code():
    """非六位数字/非 .SH|.SZ 后缀的 sec_code 一律 422。"""
    for bad in ("5103001.SH", "ABC123.SH", "510300.HK", "5103OO.SH", "5103 0.SH"):
        response = client.post(
            "/api/universe",
            json={"items": [{"sec_code": bad, "sec_name": "x"}]},
        )
        assert response.status_code == 422, bad


def test_add_universe_rejects_non_ascii_digits():
    """全角数字（非 ASCII）拒绝。"""
    response = client.post(
        "/api/universe",
        json={"items": [{"sec_code": "５１０３００.SH", "sec_name": "全角数字"}]},
    )
    assert response.status_code == 422


def test_add_universe_mixed_batch_rejected_entirely():
    """混合合法/非法批次在 Pydantic 入口整体 422，合法项不得先写入。"""
    valid_code = "560010.SH"  # 不在默认种子池中
    response = client.post(
        "/api/universe",
        json={
            "items": [
                {"sec_code": valid_code, "sec_name": "合法标的"},
                {"sec_code": "x'); DELETE FROM universe_items;--", "sec_name": "注入"},
            ]
        },
    )
    assert response.status_code == 422
    codes = [item["sec_code"] for item in client.get("/api/universe").json()]
    assert valid_code not in codes, "合法项不得随非法批次部分写入"


def test_add_and_remove_valid_sec_code():
    """合法 sec_code 增删可用（响应保留 str 类型）。"""
    code = "560010.SH"  # 不在默认种子池中
    response = client.post(
        "/api/universe",
        json={"items": [{"sec_code": code, "sec_name": "测试ETF"}]},
    )
    assert response.status_code == 200
    data = response.json()
    assert any(item["sec_code"] == code for item in data)
    assert isinstance(next(i["sec_code"] for i in data if i["sec_code"] == code), str)

    response = client.delete(f"/api/universe/{code}")
    assert response.status_code == 200
    assert response.json()["success"] is True
    codes = [item["sec_code"] for item in client.get("/api/universe").json()]
    assert code not in codes
