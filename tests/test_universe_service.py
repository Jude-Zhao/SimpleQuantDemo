"""Tests for universe_service."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.models.database import Base
from webapp.schemas.universe import UniverseItemCreate
from webapp.services.universe_service import (
    DEFAULT_UNIVERSE,
    add_universe_item,
    batch_add_universe,
    get_universe_codes,
    get_universe_item,
    list_active_universe,
    remove_universe_item,
    seed_default_universe,
)


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_add_universe_item(test_db):
    item = add_universe_item(test_db, UniverseItemCreate(
        sec_code="510300.SH",
        sec_name="沪深300ETF",
        meta={"category": "宽基"},
    ))
    assert item.id is not None
    assert item.sec_code == "510300.SH"
    assert item.sec_name == "沪深300ETF"
    assert item.is_active is True
    assert item.meta["category"] == "宽基"


def test_list_active_universe(test_db):
    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))
    add_universe_item(test_db, UniverseItemCreate(sec_code="510500.SH", sec_name="中证500ETF"))

    items = list_active_universe(test_db)
    assert len(items) == 2
    codes = [i.sec_code for i in items]
    assert "510300.SH" in codes
    assert "510500.SH" in codes


def test_get_universe_item(test_db):
    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))

    item = get_universe_item(test_db, "510300.SH")
    assert item is not None
    assert item.sec_name == "沪深300ETF"

    assert get_universe_item(test_db, "999999.SH") is None


def test_remove_universe_item(test_db):
    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))

    result = remove_universe_item(test_db, "510300.SH")
    assert result is True

    item = get_universe_item(test_db, "510300.SH")
    assert item is not None
    assert item.is_active is False
    assert item.removed_at is not None

    # Active list should be empty now
    assert len(list_active_universe(test_db)) == 0


def test_remove_nonexistent_returns_false(test_db):
    assert remove_universe_item(test_db, "999999.SH") is False


def test_reactivate_removed_item(test_db):
    """Adding an item that was previously removed should reactivate it."""
    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))
    remove_universe_item(test_db, "510300.SH")
    assert len(list_active_universe(test_db)) == 0

    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))
    assert len(list_active_universe(test_db)) == 1
    item = get_universe_item(test_db, "510300.SH")
    assert item.is_active is True
    assert item.removed_at is None


def test_batch_add_universe(test_db):
    items = [
        UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"),
        UniverseItemCreate(sec_code="510500.SH", sec_name="中证500ETF"),
        UniverseItemCreate(sec_code="159915.SZ", sec_name="创业板ETF"),
    ]
    result = batch_add_universe(test_db, items)
    assert len(result) == 3
    assert len(list_active_universe(test_db)) == 3


def test_get_universe_codes(test_db):
    add_universe_item(test_db, UniverseItemCreate(sec_code="510300.SH", sec_name="沪深300ETF"))
    add_universe_item(test_db, UniverseItemCreate(sec_code="510500.SH", sec_name="中证500ETF"))

    codes = get_universe_codes(test_db)
    assert codes == ["510300.SH", "510500.SH"]


def test_seed_default_universe(test_db):
    assert len(list_active_universe(test_db)) == 0
    seed_default_universe(test_db)
    items = list_active_universe(test_db)
    assert len(items) == len(DEFAULT_UNIVERSE)
    assert len(items) >= 5


def test_seed_default_universe_idempotent(test_db):
    seed_default_universe(test_db)
    count1 = len(list_active_universe(test_db))
    seed_default_universe(test_db)
    count2 = len(list_active_universe(test_db))
    assert count1 == count2  # Should not duplicate
