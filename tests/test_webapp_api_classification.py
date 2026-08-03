"""Tests for classification and constraints API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from webapp.main import app

client = TestClient(app)


def _add_universe_item(code: str, name: str, meta: dict | None = None) -> None:
    """Helper to add an item to the universe."""
    client.post("/api/universe", json={
        "items": [
            {"sec_code": code, "sec_name": name, "meta": meta or {}},
        ]
    })


def _delete_universe_item(code: str) -> None:
    """Helper to remove an item from the universe."""
    client.delete(f"/api/universe/{code}")


def test_list_rules_empty():
    response = client.get("/api/classifications/rules")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_rule():
    response = client.post("/api/classifications/rules", json={
        "rule_name": "宽基分类",
        "category_key": "category",
        "rule_type": "manual",
        "config": {
            "sec_codes": ["510300.SH", "510500.SH"],
            "category_value": "宽基",
        },
        "priority": 10,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["rule_name"] == "宽基分类"
    assert data["category_key"] == "category"
    assert data["rule_type"] == "manual"
    assert data["is_active"] is True
    assert data["priority"] == 10
    assert "id" in data


def test_update_rule():
    # Create a rule first
    resp = client.post("/api/classifications/rules", json={
        "rule_name": "待更新规则",
        "category_key": "category",
        "rule_type": "manual",
        "config": {"sec_codes": [], "category_value": "测试"},
    })
    rule_id = resp.json()["id"]

    response = client.put(f"/api/classifications/rules/{rule_id}", json={
        "rule_name": "已更新规则",
        "priority": 5,
    })
    assert response.status_code == 200
    data = response.json()
    assert data["rule_name"] == "已更新规则"
    assert data["priority"] == 5


def test_update_rule_not_found():
    response = client.put("/api/classifications/rules/999999", json={
        "rule_name": "不存在",
    })
    assert response.status_code == 404


def test_delete_rule():
    # Create a rule first
    resp = client.post("/api/classifications/rules", json={
        "rule_name": "待删除规则",
        "category_key": "category",
        "rule_type": "manual",
        "config": {"sec_codes": [], "category_value": "测试"},
    })
    rule_id = resp.json()["id"]

    response = client.delete(f"/api/classifications/rules/{rule_id}")
    assert response.status_code == 200
    assert response.json()["success"] is True

    # Verify gone
    response = client.delete(f"/api/classifications/rules/{rule_id}")
    assert response.status_code == 404


def test_apply_classification():
    # Ensure universe has items (cleanup first)
    _delete_universe_item("510300.SH")
    _delete_universe_item("510500.SH")
    _add_universe_item("510300.SH", "沪深300ETF", {"category": "宽基"})
    _add_universe_item("510500.SH", "中证500ETF", {"category": "宽基"})

    response = client.post("/api/classifications/apply")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # Both universe items should appear
    codes = {item["sec_code"] for item in data}
    assert "510300.SH" in codes
    assert "510500.SH" in codes


def test_get_constraints():
    response = client.get("/api/constraints")
    assert response.status_code == 200
    data = response.json()
    assert "single_max_weight" in data
    assert "category_constraints" in data
    assert isinstance(data["category_constraints"], list)


def test_update_constraints():
    response = client.put("/api/constraints", json={
        "constraints": {
            "single_max_weight": 0.2,
            "single_min_weight": 0.01,
            "turnover_limit": 0.5,
            "category_constraints": [
                {
                    "category_key": "category",
                    "category_value": "宽基",
                    "max_weight": 0.4,
                    "min_weight": 0.1,
                },
                {
                    "category_key": "category",
                    "category_value": "商品",
                    "max_weight": 0.3,
                },
            ],
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["single_max_weight"] == 0.2
    assert data["single_min_weight"] == 0.01
    assert data["turnover_limit"] == 0.5
    assert len(data["category_constraints"]) == 2
    assert data["category_constraints"][0]["category_value"] == "宽基"
    assert data["category_constraints"][0]["max_weight"] == 0.4

    # Verify GET returns the updated value
    response = client.get("/api/constraints")
    assert response.status_code == 200
    assert response.json()["single_max_weight"] == 0.2
