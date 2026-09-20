"""Classification rule engine and constraints service."""

from __future__ import annotations

import logging
import math

from sqlalchemy.orm import Session

from webapp.models.database import utc_now
from webapp.models.classification import ClassificationRule
from webapp.models.universe import UniverseItem
from webapp.schemas.classification import (
    RULE_TYPES,
    ClassificationRuleCreate,
    ClassificationRuleUpdate,
    ClassificationResult,
)

logger = logging.getLogger(__name__)


# ── Config validation (F14) ────────────────────────────────────────────

def _validate_config(rule_type: str, config: object) -> None:
    """按 rule_type 校验 config 结构，非法配置在写库前拒绝（ValueError → API 400）。

    键均可选（引擎对缺失键按空配置处理），但已知键的类型必须合法，重点阻止
    三类已证实的崩溃形态：config 非 dict（classify_universe 稳定 AttributeError）、
    manual 的 sec_codes 非字符串列表（含 null，list(None) 崩溃）、by_range 的
    min/max 非有限数值或区间为空（float() 崩溃/永不匹配）。未知键不约束。
    """
    if rule_type not in RULE_TYPES:
        raise ValueError(f"rule_type 必须为 {'/'.join(RULE_TYPES)} 之一，收到 {rule_type!r}")
    if not isinstance(config, dict):
        raise ValueError(f"config 必须为字典，收到 {config!r}（不允许 null 或其他类型）")
    category_value = config.get("category_value")
    if category_value is not None and not isinstance(category_value, str):
        raise ValueError(f"category_value 必须为字符串，收到 {category_value!r}")
    if rule_type == "manual":
        if "sec_codes" in config:
            sec_codes = config["sec_codes"]
            if not isinstance(sec_codes, list) or any(
                not isinstance(code, str) for code in sec_codes
            ):
                raise ValueError(f"manual 规则的 sec_codes 必须为字符串列表，收到 {sec_codes!r}")
    elif rule_type == "by_field":
        _validate_lookup_field(config, "by_field")
    elif rule_type == "by_range":
        _validate_lookup_field(config, "by_range")
        bounds: dict[str, float] = {}
        for key in ("min", "max"):
            bound = config.get(key)
            if bound is None:
                continue
            if (
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or not math.isfinite(bound)
            ):
                raise ValueError(f"by_range 规则的 {key} 必须为有限数值，收到 {bound!r}")
            bounds[key] = float(bound)
        if "min" in bounds and "max" in bounds and bounds["min"] >= bounds["max"]:
            raise ValueError(
                f"by_range 规则要求 min < max，收到 min={bounds['min']}, max={bounds['max']}"
            )


def _validate_lookup_field(config: dict, rule_type: str) -> None:
    field = config.get("field")
    if field is not None and (not isinstance(field, str) or not field):
        raise ValueError(f"{rule_type} 规则的 field 必须为非空字符串，收到 {field!r}")


# ── Rule CRUD ──────────────────────────────────────────────────────────

def list_rules(db: Session, active_only: bool = True) -> list[ClassificationRule]:
    """List all classification rules, ordered by priority."""
    query = db.query(ClassificationRule)
    if active_only:
        query = query.filter(ClassificationRule.is_active == True)
    return query.order_by(ClassificationRule.priority, ClassificationRule.id).all()


def get_rule(db: Session, rule_id: int) -> ClassificationRule | None:
    """Get a single rule by ID."""
    return db.query(ClassificationRule).filter(ClassificationRule.id == rule_id).first()


def create_rule(db: Session, rule: ClassificationRuleCreate) -> ClassificationRule:
    """Create a new classification rule."""
    # F14: schema 层已挡类型/null，这里兜底校验 (rule_type, config) 结构组合。
    _validate_config(rule.rule_type, rule.config)
    db_rule = ClassificationRule(
        rule_name=rule.rule_name,
        category_key=rule.category_key,
        rule_type=rule.rule_type,
        config=rule.config,
        is_active=rule.is_active,
        priority=rule.priority,
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    return db_rule


def update_rule(db: Session, rule_id: int, update: ClassificationRuleUpdate) -> ClassificationRule | None:
    """Update an existing classification rule."""
    rule = get_rule(db, rule_id)
    if rule is None:
        return None

    update_data = update.model_dump(exclude_unset=True)
    # F14: rule_type 或 config 变更时，合并后的 (类型, 配置) 组合必须合法，
    # 校验先于任何 setattr/commit——失败更新不改变旧规则。两者均未提交时
    # 不重新校验（遗留坏记录由读路径隔离，不阻塞无关字段的修复）。
    if "rule_type" in update_data or "config" in update_data:
        effective_type = update_data.get("rule_type", rule.rule_type)
        effective_config = update_data.get("config", rule.config)
        _validate_config(effective_type, effective_config)
    for key, value in update_data.items():
        setattr(rule, key, value)
    rule.updated_at = utc_now()
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: int) -> bool:
    """Delete a classification rule."""
    rule = get_rule(db, rule_id)
    if rule is None:
        return False
    db.delete(rule)
    db.commit()
    return True


# ── Classification Engine ──────────────────────────────────────────────

def ensure_classification(
    db: Session,
    sec_code: str,
    categories: dict[str, str],
) -> None:
    """Assign ``categories`` to ``sec_code`` by upserting manual rules.

    For each (category_key, category_value) pair, appends ``sec_code`` to the
    ``sec_codes`` of the matching manual rule, creating the rule if none
    exists. This keeps the rule engine the single source of truth so a newly
    added ETF is classified immediately and stays consistent with the
    classification page.
    """
    if not categories:
        return
    rules = []
    for r in (
        db.query(ClassificationRule)
        .filter(ClassificationRule.rule_type == "manual")
        .all()
    ):
        if isinstance(r.config, dict):
            rules.append(r)
        else:
            # F14: 遗留损坏记录隔离，不让单条坏规则阻塞标的写入。
            logger.warning(
                "manual 规则 id=%s name=%r 的 config 非 dict（遗留损坏数据），已跳过",
                r.id, r.rule_name,
            )
    for category_key, category_value in categories.items():
        if not category_value:
            continue
        rule = next(
            (
                r
                for r in rules
                if r.category_key == category_key
                and r.config.get("category_value") == category_value
            ),
            None,
        )
        if rule is None:
            rule = ClassificationRule(
                rule_name=f"{category_key}-{category_value}",
                category_key=category_key,
                rule_type="manual",
                config={"category_value": category_value, "sec_codes": []},
                is_active=True,
                priority=100,
            )
            db.add(rule)
            rules.append(rule)
            db.flush()
        # Assign a new dict (not in-place mutation): plain JSON columns do not
        # track in-place edits, so mutating the existing dict would silently
        # fail to persist the appended code.
        sec_codes = rule.config.get("sec_codes", [])
        if not isinstance(sec_codes, list):
            logger.warning(
                "manual 规则 id=%s 的 sec_codes 非列表（遗留损坏数据），重建为空列表",
                rule.id,
            )
            sec_codes = []
        if sec_code not in sec_codes:
            rule.config = {**rule.config, "sec_codes": [*sec_codes, sec_code]}
    db.commit()


def classify_universe(db: Session) -> list[ClassificationResult]:
    """Apply all active classification rules to the active universe.

    Rules are executed in priority order (lower number = higher priority).
    For each category key, higher-priority rules override lower ones.
    """
    rules = list_rules(db, active_only=True)
    universe_items = (
        db.query(UniverseItem)
        .filter(UniverseItem.is_active == True)
        .order_by(UniverseItem.sec_code)
        .all()
    )

    # Build initial classification: {sec_code: {category_key: category_value}}
    classifications: dict[str, dict[str, str]] = {
        item.sec_code: {} for item in universe_items
    }

    # Build metadata lookup for by_field / by_range rules
    meta_by_sec = {
        item.sec_code: item.meta or {} for item in universe_items
    }

    for rule in rules:
        matched = _match_rule(rule, universe_items, meta_by_sec)
        for sec_code in matched:
            if sec_code not in classifications:
                continue
            category_value = rule.config.get("category_value", "")
            if not category_value:
                continue
            # Only set if not already set by a higher-priority rule
            # (rules are sorted by priority ascending, so earlier = higher priority)
            if rule.category_key not in classifications[sec_code]:
                classifications[sec_code][rule.category_key] = category_value

    return [
        ClassificationResult(sec_code=code, categories=cats)
        for code, cats in classifications.items()
    ]


def _match_rule(
    rule: ClassificationRule,
    universe_items: list[UniverseItem],
    meta_by_sec: dict[str, dict],
) -> list[str]:
    """Return list of sec_codes matched by a rule."""
    if not isinstance(rule.config, dict):
        # F14: 遗留损坏记录（如历史版本持久化的 config=null）隔离跳过，
        # 不让单条坏规则使 classify_universe 全局崩溃。
        logger.warning(
            "分类规则 id=%s name=%r 的 config 非 dict（遗留损坏数据），已跳过",
            rule.id, rule.rule_name,
        )
        return []
    if rule.rule_type == "manual":
        return _match_manual(rule)
    elif rule.rule_type == "by_field":
        return _match_by_field(rule, universe_items, meta_by_sec)
    elif rule.rule_type == "by_range":
        return _match_by_range(rule, universe_items, meta_by_sec)
    else:
        return []


def _match_manual(rule: ClassificationRule) -> list[str]:
    """Match by explicit list of sec_codes."""
    sec_codes = rule.config.get("sec_codes", [])
    if sec_codes is None:
        sec_codes = []
    if not isinstance(sec_codes, list):
        logger.warning(
            "manual 规则 id=%s name=%r 的 sec_codes 非列表（遗留损坏数据），已跳过",
            rule.id, rule.rule_name,
        )
        return []
    return list(sec_codes)


def _match_by_field(
    rule: ClassificationRule,
    universe_items: list[UniverseItem],
    meta_by_sec: dict[str, dict],
) -> list[str]:
    """Match by meta field equality."""
    field = rule.config.get("field", "")
    value = rule.config.get("value", "")
    if not field:
        return []

    matched = []
    for item in universe_items:
        meta = meta_by_sec.get(item.sec_code, {})
        if str(meta.get(field, "")) == str(value):
            matched.append(item.sec_code)
        # Also check top-level fields like sec_name
        elif hasattr(item, field) and str(getattr(item, field, "")) == str(value):
            matched.append(item.sec_code)
    return matched


def _match_by_range(
    rule: ClassificationRule,
    universe_items: list[UniverseItem],
    meta_by_sec: dict[str, dict],
) -> list[str]:
    """Match by numeric field range (min <= value < max)."""
    field = rule.config.get("field", "")
    min_val = rule.config.get("min")
    max_val = rule.config.get("max")
    if not field:
        return []

    try:
        lo = float(min_val) if min_val is not None else None
        hi = float(max_val) if max_val is not None else None
    except (TypeError, ValueError):
        logger.warning(
            "by_range 规则 id=%s name=%r 的 min/max 无法解析为数值（遗留损坏数据），已跳过",
            rule.id, rule.rule_name,
        )
        return []

    matched = []
    for item in universe_items:
        meta = meta_by_sec.get(item.sec_code, {})
        raw_val = meta.get(field)
        if raw_val is None:
            continue
        try:
            val = float(raw_val)
        except (ValueError, TypeError):
            continue
        if lo is not None and val < lo:
            continue
        if hi is not None and val >= hi:
            continue
        matched.append(item.sec_code)
    return matched
