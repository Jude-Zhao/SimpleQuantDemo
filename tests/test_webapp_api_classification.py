"""Tests for classification and constraints API endpoints."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from pydantic import ValidationError

from webapp.main import app
from webapp.models.classification import ClassificationRule
from webapp.models.database import SessionLocal
from webapp.schemas.classification import CategoryConstraint, OptimizationConstraints

pytestmark = pytest.mark.usefixtures("webapp_clean_state")

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


def test_get_classification_readonly():
    """GET /api/classifications returns the read-only classification view."""
    _delete_universe_item("510300.SH")
    _add_universe_item("510300.SH", "沪深300ETF", {"category": "宽基"})

    response = client.get("/api/classifications")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    codes = {item["sec_code"] for item in data}
    assert "510300.SH" in codes
    # Mirror of the apply endpoint payload shape.
    sample = next(item for item in data if item["sec_code"] == "510300.SH")
    assert isinstance(sample["categories"], dict)


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


# ── BUG-16：约束保存无损往返（0/空/非法/Infinity/GET→PUT→GET 一致）────────

def test_update_constraints_zero_and_hidden_field_roundtrip():
    """0 必须保留为 0（前端历史 bug 是 parseFloat(...) || null 把 0 变 null，
    后端必须能无损保存 0），隐藏字段 turnover_limit=0 完整往返。"""
    payload = {
        "single_min_weight": 0,
        "single_max_weight": 0,
        "turnover_limit": 0,
        "category_constraints": [
            {
                "category_key": "category",
                "category_value": "宽基",
                "min_weight": 0,
                "max_weight": 0.5,
                "min_count": 0,
                "max_count": 2,
            }
        ],
    }
    response = client.put("/api/constraints", json={"constraints": payload})
    assert response.status_code == 200
    data = response.json()
    assert data["single_min_weight"] == 0
    assert data["single_max_weight"] == 0
    assert data["turnover_limit"] == 0
    assert data["category_constraints"][0]["min_weight"] == 0
    assert data["category_constraints"][0]["min_count"] == 0

    # GET → 不修改 → PUT → GET 完全一致（前端快照保存的等价行为）
    first = client.get("/api/constraints")
    assert first.status_code == 200
    put_again = client.put("/api/constraints", json={"constraints": first.json()})
    assert put_again.status_code == 200
    assert put_again.json() == first.json()
    assert client.get("/api/constraints").json() == first.json()


def test_update_constraints_null_fields_roundtrip():
    """空串在前端解析为 null；null 约束往返不变形。"""
    payload = {
        "single_min_weight": None,
        "single_max_weight": 0.2,
        "turnover_limit": None,
        "category_constraints": [],
    }
    response = client.put("/api/constraints", json={"constraints": payload})
    assert response.status_code == 200
    data = response.json()
    assert data["single_min_weight"] is None
    assert data["turnover_limit"] is None
    assert client.get("/api/constraints").json()["single_max_weight"] == 0.2


def test_update_constraints_rejects_infinity_and_nan():
    # 说明（二选一：直接测 schema 层校验）：
    # Infinity/NaN 不是合法 JSON 值（RFC 8259），正常客户端无法经严格 JSON 传输：
    # - 本项目 TestClient（httpx）序列化请求体时 allow_nan=False，json= 传 inf
    #   在客户端即抛 ValueError（请求根本不会发出）；
    # - 若手工构造含 Infinity 字面量的原始请求体（Python json.loads 会接受），
    #   Pydantic 能正确拒绝，但 FastAPI 生成的 422 错误详情包含原始 input=inf，
    #   而当前 starlette 版本渲染 JSONResponse 时 allow_nan=False，导致错误响应
    #   本身无法序列化（500）——修此问题需改 API 层异常处理器，超出本阶段允许
    #   修改的文件边界。因此本用例直接验证 schema 层的有限值校验（权威防线）。
    with pytest.raises(ValidationError):
        OptimizationConstraints(single_max_weight=float("inf"))
    with pytest.raises(ValidationError):
        OptimizationConstraints(single_min_weight=float("-inf"))
    with pytest.raises(ValidationError):
        OptimizationConstraints(single_min_weight=float("nan"))
    with pytest.raises(ValidationError):
        OptimizationConstraints(turnover_limit=float("inf"))
    with pytest.raises(ValidationError):
        OptimizationConstraints(
            category_constraints=[
                CategoryConstraint(
                    category_key="category",
                    category_value="宽基",
                    max_weight=float("inf"),
                )
            ]
        )


def test_update_constraints_rejects_non_integer_count():
    """数量必须是整数（1.5 → 422）。"""
    response = client.put(
        "/api/constraints",
        json={
            "constraints": {
                "category_constraints": [
                    {
                        "category_key": "category",
                        "category_value": "宽基",
                        "min_count": 1.5,
                    }
                ]
            }
        },
    )
    assert response.status_code == 422


def test_update_constraints_category_constraints_must_be_list():
    """category_constraints 必须是数组（对象 → 422）。"""
    response = client.put(
        "/api/constraints",
        json={
            "constraints": {
                "category_constraints": {"category_key": "category", "category_value": "宽基"},
            }
        },
    )
    assert response.status_code == 422


def test_update_constraints_allows_infeasible_config():
    """约束只是提示模式：不可满足（min>max、min_count>max_count）不得禁止配置。"""
    response = client.put(
        "/api/constraints",
        json={
            "constraints": {
                "category_constraints": [
                    {
                        "category_key": "category",
                        "category_value": "宽基",
                        "min_weight": 0.5,
                        "max_weight": 0.1,
                        "min_count": 5,
                        "max_count": 1,
                    }
                ]
            }
        },
    )
    assert response.status_code == 200
    item = response.json()["category_constraints"][0]
    assert item["min_weight"] == 0.5
    assert item["max_weight"] == 0.1
    assert item["min_count"] == 5
    assert item["max_count"] == 1


# ── F14: 非法分类配置写库前拒绝 + 遗留坏记录不阻塞管理页 ────────────────


def test_update_rule_rejects_null_config():
    """PUT config=null → 422（审计主证据：持久化后 classify_universe 全局崩）。"""
    resp = client.post("/api/classifications/rules", json={
        "rule_name": "规则", "category_key": "size", "rule_type": "manual",
        "config": {"sec_codes": [], "category_value": "大盘"},
    })
    rule_id = resp.json()["id"]

    response = client.put(f"/api/classifications/rules/{rule_id}", json={"config": None})
    assert response.status_code == 422

    rules = client.get("/api/classifications/rules").json()
    target = next(r for r in rules if r["id"] == rule_id)
    assert target["config"] == {"sec_codes": [], "category_value": "大盘"}


def test_create_rule_rejects_invalid_config():
    for config in (
        {"sec_codes": None},                                # list(None) 崩溃
        {"sec_codes": "510300.SH"},                         # 字符串非列表
        {"field": "fund_size", "min": "abc"},               # float() 崩溃
        {"field": "fund_size", "min": 5, "max": 5},         # 空区间
    ):
        response = client.post("/api/classifications/rules", json={
            "rule_name": "坏规则", "category_key": "size", "rule_type": "manual",
            "config": config,
        } if "sec_codes" in config else {
            "rule_name": "坏规则", "category_key": "size", "rule_type": "by_range",
            "config": config,
        })
        assert response.status_code == 400, config

    # 合法配置不受影响
    response = client.post("/api/classifications/rules", json={
        "rule_name": "好规则", "category_key": "size", "rule_type": "manual",
        "config": {"sec_codes": [], "category_value": "大盘"},
    })
    assert response.status_code == 200


def test_update_rule_rejects_invalid_config_keeps_old():
    resp = client.post("/api/classifications/rules", json={
        "rule_name": "规则", "category_key": "size", "rule_type": "manual",
        "config": {"sec_codes": ["510300.SH"], "category_value": "大盘"},
    })
    rule_id = resp.json()["id"]

    response = client.put(f"/api/classifications/rules/{rule_id}", json={
        "config": {"sec_codes": None},
    })
    assert response.status_code == 400

    rules = client.get("/api/classifications/rules").json()
    target = next(r for r in rules if r["id"] == rule_id)
    assert target["config"] == {"sec_codes": ["510300.SH"], "category_value": "大盘"}


def test_rules_endpoints_survive_legacy_bad_config():
    """含遗留坏记录时：规则列表/分类视图/应用接口全部可用（管理页可定位修复）。"""
    db = SessionLocal()
    try:
        db.add(ClassificationRule(
            rule_name="坏config", category_key="size", rule_type="manual", config=None,
        ))
        db.add(ClassificationRule(
            rule_name="坏范围", category_key="size_bucket", rule_type="by_range",
            config={"field": "fund_size", "min": "abc", "category_value": "大规模"},
        ))
        db.commit()

        rules = client.get("/api/classifications/rules")
        assert rules.status_code == 200  # 管理页数据源不炸
        bad = next(r for r in rules.json() if r["rule_name"] == "坏config")
        assert bad["config"] is None  # 读模型容忍 null，前端可打开编辑修复

        assert client.get("/api/classifications").status_code == 200
        assert client.post("/api/classifications/apply").status_code == 200
    finally:
        db.query(ClassificationRule).delete()
        db.commit()
        db.close()
