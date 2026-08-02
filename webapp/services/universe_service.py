"""Universe management service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from webapp.models.universe import UniverseItem
from webapp.schemas.universe import UniverseItemCreate


DEFAULT_UNIVERSE = [
    UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="510500.SH", sec_name="中证500ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="159915.SZ", sec_name="创业板ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="518880.SH", sec_name="黄金ETF", meta={"category": "商品"}),
    UniverseItemCreate(sec_code="511010.SH", sec_name="国债ETF", meta={"category": "债券"}),
    UniverseItemCreate(sec_code="510050.SH", sec_name="上证50ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="510880.SH", sec_name="红利ETF", meta={"category": "策略"}),
    UniverseItemCreate(sec_code="159901.SZ", sec_name="深100ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="510180.SH", sec_name="上证180ETF", meta={"category": "宽基"}),
    UniverseItemCreate(sec_code="159919.SZ", sec_name="沪深300ETF(嘉实)", meta={"category": "宽基"}),
]


def seed_default_universe(db: Session) -> None:
    """Seed the universe with default ETFs if empty."""
    existing = db.query(UniverseItem).first()
    if existing is not None:
        return
    for item in DEFAULT_UNIVERSE:
        add_universe_item(db, item)


def list_active_universe(db: Session) -> list[UniverseItem]:
    """Get all active universe items."""
    return (
        db.query(UniverseItem)
        .filter(UniverseItem.is_active == True)
        .order_by(UniverseItem.sec_code)
        .all()
    )


def list_all_universe(db: Session) -> list[UniverseItem]:
    """Get all universe items (including inactive)."""
    return db.query(UniverseItem).order_by(UniverseItem.sec_code).all()


def get_universe_item(db: Session, sec_code: str) -> UniverseItem | None:
    """Get a single universe item by code."""
    return db.query(UniverseItem).filter(UniverseItem.sec_code == sec_code).first()


def add_universe_item(db: Session, item: UniverseItemCreate) -> UniverseItem:
    """Add an ETF to the universe.

    If the item already exists (inactive), reactivate it.
    """
    existing = get_universe_item(db, item.sec_code)
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.removed_at = None
            existing.sec_name = item.sec_name
            existing.meta = item.meta
            db.commit()
            db.refresh(existing)
        return existing

    db_item = UniverseItem(
        sec_code=item.sec_code,
        sec_name=item.sec_name,
        meta=item.meta,
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def batch_add_universe(db: Session, items: list[UniverseItemCreate]) -> list[UniverseItem]:
    """Batch add ETFs to the universe."""
    result = []
    for item in items:
        result.append(add_universe_item(db, item))
    return result


def remove_universe_item(db: Session, sec_code: str) -> bool:
    """Remove an ETF from the universe (soft delete).

    Returns True if removed, False if not found.
    """
    item = get_universe_item(db, sec_code)
    if not item or not item.is_active:
        return False

    item.is_active = False
    item.removed_at = datetime.utcnow()
    db.commit()
    return True


def get_universe_codes(db: Session) -> list[str]:
    """Get list of active ETF codes (convenience function for strategies)."""
    items = list_active_universe(db)
    return [item.sec_code for item in items]
