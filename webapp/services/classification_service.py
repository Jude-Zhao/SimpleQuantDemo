"""Classification rule engine and constraints service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from webapp.models.classification import ClassificationRule
from webapp.models.universe import UniverseItem
from webapp.schemas.classification import (
    ClassificationRuleCreate,
    ClassificationRuleUpdate,
    ClassificationResult,
)


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
    for key, value in update_data.items():
        setattr(rule, key, value)
    rule.updated_at = datetime.utcnow()
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
    rules = (
        db.query(ClassificationRule)
        .filter(ClassificationRule.rule_type == "manual")
        .all()
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
        if min_val is not None and val < float(min_val):
            continue
        if max_val is not None and val >= float(max_val):
            continue
        matched.append(item.sec_code)
    return matched
