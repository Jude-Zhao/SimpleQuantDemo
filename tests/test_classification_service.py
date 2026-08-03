"""Tests for classification_service."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.models.database import Base
from webapp.models.universe import UniverseItem
from webapp.schemas.classification import ClassificationRuleCreate, ClassificationRuleUpdate
from webapp.services.classification_service import (
    classify_universe,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_rule,
)


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()

    # Seed universe items
    items = [
        UniverseItem(sec_code="510300.SH", sec_name="沪深300ETF", meta={"category": "宽基", "fund_size": 800}),
        UniverseItem(sec_code="510500.SH", sec_name="中证500ETF", meta={"category": "宽基", "fund_size": 600}),
        UniverseItem(sec_code="159915.SZ", sec_name="创业板ETF", meta={"category": "宽基", "fund_size": 300}),
        UniverseItem(sec_code="518880.SH", sec_name="黄金ETF", meta={"category": "商品", "fund_size": 200}),
        UniverseItem(sec_code="511010.SH", sec_name="国债ETF", meta={"category": "债券", "fund_size": 150}),
    ]
    for item in items:
        db.add(item)
    db.commit()

    try:
        yield db
    finally:
        db.close()


def test_create_rule(test_db):
    rule = create_rule(test_db, ClassificationRuleCreate(
        rule_name="大盘分类",
        category_key="size",
        rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "大盘"},
        priority=10,
    ))
    assert rule.id is not None
    assert rule.rule_name == "大盘分类"
    assert rule.category_key == "size"
    assert rule.rule_type == "manual"
    assert rule.is_active is True


def test_list_rules(test_db):
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="规则1", category_key="size", rule_type="manual",
        config={}, priority=20,
    ))
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="规则2", category_key="size", rule_type="manual",
        config={}, priority=10,
    ))

    rules = list_rules(test_db)
    assert len(rules) == 2
    # Sorted by priority ascending
    assert rules[0].priority == 10
    assert rules[1].priority == 20


def test_get_rule(test_db):
    created = create_rule(test_db, ClassificationRuleCreate(
        rule_name="测试规则", category_key="size", rule_type="manual", config={},
    ))
    fetched = get_rule(test_db, created.id)
    assert fetched is not None
    assert fetched.rule_name == "测试规则"

    assert get_rule(test_db, 9999) is None


def test_update_rule(test_db):
    created = create_rule(test_db, ClassificationRuleCreate(
        rule_name="旧名称", category_key="size", rule_type="manual", config={},
    ))
    updated = update_rule(test_db, created.id, ClassificationRuleUpdate(
        rule_name="新名称", priority=50,
    ))
    assert updated is not None
    assert updated.rule_name == "新名称"
    assert updated.priority == 50

    assert update_rule(test_db, 9999, ClassificationRuleUpdate()) is None


def test_delete_rule(test_db):
    created = create_rule(test_db, ClassificationRuleCreate(
        rule_name="待删除", category_key="size", rule_type="manual", config={},
    ))
    assert delete_rule(test_db, created.id) is True
    assert get_rule(test_db, created.id) is None
    assert delete_rule(test_db, 9999) is False


def test_classify_manual_rule(test_db):
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="大盘",
        category_key="size",
        rule_type="manual",
        config={"sec_codes": ["510300.SH", "510500.SH"], "category_value": "大盘"},
        priority=10,
    ))

    results = classify_universe(test_db)
    assert len(results) == 5
    by_code = {r.sec_code: r for r in results}

    assert by_code["510300.SH"].categories.get("size") == "大盘"
    assert by_code["510500.SH"].categories.get("size") == "大盘"
    assert by_code["159915.SZ"].categories.get("size") is None  # Not matched


def test_classify_by_field_rule(test_db):
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="按类别字段",
        category_key="asset_type",
        rule_type="by_field",
        config={"field": "category", "value": "商品", "category_value": "商品类"},
        priority=10,
    ))

    results = classify_universe(test_db)
    by_code = {r.sec_code: r for r in results}
    assert by_code["518880.SH"].categories.get("asset_type") == "商品类"
    assert by_code["510300.SH"].categories.get("asset_type") is None


def test_classify_by_range_rule(test_db):
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="大规模",
        category_key="size_bucket",
        rule_type="by_range",
        config={"field": "fund_size", "min": 500, "category_value": "大规模"},
        priority=10,
    ))

    results = classify_universe(test_db)
    by_code = {r.sec_code: r for r in results}
    assert by_code["510300.SH"].categories.get("size_bucket") == "大规模"  # 800
    assert by_code["510500.SH"].categories.get("size_bucket") == "大规模"  # 600
    assert by_code["159915.SZ"].categories.get("size_bucket") is None  # 300


def test_classify_priority_overrides(test_db):
    """Higher-priority rule (lower number) should win for same category_key."""
    # Low priority rule (higher number)
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="低优先级",
        category_key="size",
        rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "中盘"},
        priority=100,
    ))
    # High priority rule (lower number)
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="高优先级",
        category_key="size",
        rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "大盘"},
        priority=10,
    ))

    results = classify_universe(test_db)
    by_code = {r.sec_code: r for r in results}
    # High priority wins
    assert by_code["510300.SH"].categories.get("size") == "大盘"


def test_classify_multiple_category_keys(test_db):
    """Different category_keys should not interfere."""
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="规模",
        category_key="size",
        rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "大盘"},
        priority=10,
    ))
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="资产类型",
        category_key="asset",
        rule_type="by_field",
        config={"field": "category", "value": "债券", "category_value": "固收"},
        priority=10,
    ))

    results = classify_universe(test_db)
    by_code = {r.sec_code: r for r in results}

    assert "size" in by_code["510300.SH"].categories
    assert "asset" not in by_code["510300.SH"].categories  # 宽基 ≠ 债券

    assert "asset" in by_code["511010.SH"].categories
    assert by_code["511010.SH"].categories["asset"] == "固收"
