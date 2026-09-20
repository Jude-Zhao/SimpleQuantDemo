"""Tests for classification_service."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.models.classification import ClassificationRule
from webapp.models.database import Base
from webapp.models.universe import UniverseItem
from webapp.schemas.classification import ClassificationRuleCreate, ClassificationRuleUpdate
from webapp.services.classification_service import (
    _validate_config,
    classify_universe,
    create_rule,
    delete_rule,
    ensure_classification,
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


# ── F14: 非法分类配置写库前拒绝 + 遗留坏记录读路径隔离 ─────────────────


def test_update_rejects_explicit_null_fields():
    """提交 null 与省略字段必须区分：显式 null 一律 ValidationError（422）。"""
    with pytest.raises(ValidationError):
        ClassificationRuleUpdate(config=None)
    with pytest.raises(ValidationError):
        ClassificationRuleUpdate(rule_name=None)
    with pytest.raises(ValidationError):
        ClassificationRuleUpdate(category_key=None)
    with pytest.raises(ValidationError):
        ClassificationRuleUpdate(rule_type=None)
    # 省略字段不触发校验（部分更新语义保持）
    update = ClassificationRuleUpdate(priority=1)
    assert update.config is None and update.rule_name is None


def test_create_and_update_reject_unknown_rule_type():
    with pytest.raises(ValidationError):
        ClassificationRuleCreate(
            rule_name="x", category_key="size", rule_type="magic", config={},
        )
    with pytest.raises(ValidationError):
        ClassificationRuleUpdate(rule_type="magic")


def test_validate_config_rejects_bad_shapes():
    """三类已证实崩溃形态 + 未知 rule_type 一律拒绝；合法形态放行。"""
    bad = [
        ("manual", None),                                   # config=null（审计主证据）
        ("manual", "510300.SH"),                            # 非 dict
        ("manual", {"sec_codes": None}),                    # list(None) 崩溃
        ("manual", {"sec_codes": "510300.SH"}),             # 字符串非列表
        ("manual", {"sec_codes": [1]}),                     # 元素非字符串
        ("by_range", {"field": "f", "min": "abc"}),         # float() 崩溃
        ("by_range", {"field": "f", "min": True}),          # bool 伪装
        ("by_range", {"field": "f", "min": 5, "max": 5}),   # 空区间
        ("magic", {}),                                      # 未知类型
    ]
    for rule_type, config in bad:
        with pytest.raises(ValueError):
            _validate_config(rule_type, config)

    ok = [
        ("manual", {}),
        ("manual", {"sec_codes": [], "category_value": "大盘"}),
        ("by_field", {"field": "category", "value": "宽基"}),
        ("by_range", {"field": "fund_size", "min": 500}),
        ("by_range", {"field": "fund_size", "max": 10}),
        ("by_range", {"field": "fund_size", "min": 1, "max": 2}),
    ]
    for rule_type, config in ok:
        _validate_config(rule_type, config)


def test_update_type_change_with_incompatible_config_rejected(test_db):
    """rule_type 变更后按新类型重新校验 config：旧 config 携带对新类型
    非法的键（min="abc" 对 manual 无害、对 by_range 崩溃）→ 拒绝，旧规则原样保留。"""
    created = create_rule(test_db, ClassificationRuleCreate(
        rule_name="手动规则", category_key="size", rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "大盘", "min": "abc"},
    ))
    with pytest.raises(ValueError):
        update_rule(test_db, created.id, ClassificationRuleUpdate(rule_type="by_range"))

    rule = get_rule(test_db, created.id)
    assert rule.rule_type == "manual"
    assert rule.config == {
        "sec_codes": ["510300.SH"], "category_value": "大盘", "min": "abc",
    }


def test_update_config_only_validated_against_existing_type(test_db):
    """只改 config 时按数据库中的 rule_type 校验合并结果。"""
    created = create_rule(test_db, ClassificationRuleCreate(
        rule_name="区间规则", category_key="size", rule_type="by_range",
        config={"field": "fund_size", "min": 100, "category_value": "大盘"},
    ))
    with pytest.raises(ValueError):
        update_rule(test_db, created.id, ClassificationRuleUpdate(config={"min": "abc"}))

    rule = get_rule(test_db, created.id)
    assert rule.config["min"] == 100


def test_partial_update_allows_repair_of_legacy_bad_config(test_db):
    """遗留 config=null 的规则：只改名称不被阻塞；补交合法 config 即自愈。"""
    test_db.add(ClassificationRule(
        rule_name="遗留坏规则", category_key="size", rule_type="manual", config=None,
    ))
    test_db.commit()
    rule_id = test_db.query(ClassificationRule).filter_by(rule_name="遗留坏规则").one().id

    repaired = update_rule(test_db, rule_id, ClassificationRuleUpdate(rule_name="已修复"))
    assert repaired.rule_name == "已修复"

    fixed = update_rule(test_db, rule_id, ClassificationRuleUpdate(
        config={"category_value": "大盘", "sec_codes": ["510300.SH"]},
    ))
    assert fixed.config == {"category_value": "大盘", "sec_codes": ["510300.SH"]}
    by_code = {r.sec_code: r for r in classify_universe(test_db)}
    assert by_code["510300.SH"].categories.get("size") == "大盘"


def test_classify_universe_isolates_legacy_bad_rules(test_db):
    """遗留坏规则（config=null / sec_codes=null / min 非数值）只被跳过，
    classify_universe 不崩，好规则照常生效。"""
    create_rule(test_db, ClassificationRuleCreate(
        rule_name="好规则", category_key="size", rule_type="manual",
        config={"sec_codes": ["510300.SH"], "category_value": "大盘"},
        priority=10,
    ))
    test_db.add(ClassificationRule(
        rule_name="坏config", category_key="size", rule_type="manual", config=None,
    ))
    test_db.add(ClassificationRule(
        rule_name="坏sec_codes", category_key="size", rule_type="manual",
        config={"sec_codes": None, "category_value": "中盘"},
    ))
    test_db.add(ClassificationRule(
        rule_name="坏范围", category_key="size_bucket", rule_type="by_range",
        config={"field": "fund_size", "min": "abc", "category_value": "大规模"},
    ))
    test_db.commit()

    results = classify_universe(test_db)
    by_code = {r.sec_code: r for r in results}
    assert by_code["510300.SH"].categories.get("size") == "大盘"
    assert all(not r.categories for r in by_code.values() if r.sec_code != "510300.SH")


def test_ensure_classification_tolerates_legacy_bad_rules(test_db):
    """ensure_classification 遇到遗留坏规则不崩，匹配项自愈重建 sec_codes。"""
    test_db.add(ClassificationRule(
        rule_name="坏config", category_key="size", rule_type="manual", config=None,
    ))
    test_db.add(ClassificationRule(
        rule_name="坏sec_codes", category_key="size", rule_type="manual",
        config={"sec_codes": None, "category_value": "大盘"},
    ))
    test_db.commit()

    ensure_classification(test_db, "510300.SH", {"size": "大盘"})

    healed = (
        test_db.query(ClassificationRule)
        .filter(ClassificationRule.rule_name == "坏sec_codes")
        .one()
    )
    assert healed.config["sec_codes"] == ["510300.SH"]
