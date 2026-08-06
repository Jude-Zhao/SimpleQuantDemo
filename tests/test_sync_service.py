"""Tests for sync service (ETF market data sync)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from webapp.services.sync_service import (
    SyncStatus,
    SyncType,
    create_task,
    get_task,
    start_etf_sync,
)


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from webapp.models.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_create_and_get_task():
    task = create_task(SyncType.ETF_DAILY)
    assert task.task_id
    assert task.status == SyncStatus.PENDING
    assert get_task(task.task_id) is task
    assert get_task("nonexistent") is None


def test_task_fields_update():
    task = create_task(SyncType.ETF_MINUTE)
    task.total = 5
    task.current = 2
    task.message = "测试消息"
    fetched = get_task(task.task_id)
    assert fetched.total == 5
    assert fetched.current == 2
    assert fetched.message == "测试消息"


def test_start_etf_sync_creates_task(db):
    with patch("webapp.services.sync_service._run_etf_sync"):
        task = start_etf_sync(
            db=db,
            sec_codes=["510300.SH"],
            start_date="2024-01-02",
            end_date="2024-01-10",
        )
        assert task.task_id
        assert task.total == 1
        assert task.status in (SyncStatus.PENDING, SyncStatus.RUNNING)
        fetched = get_task(task.task_id)
        assert fetched is not None


def test_etf_sync_schema(db):
    """Sync service writes correct schema to DB."""
    from webapp.models.market_data import EtfDailyBar

    df = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-01-02"),
                "sec": "510300.SH",
                "open": 3.5,
                "high": 3.55,
                "low": 3.45,
                "close": 3.53,
                "volume": 1000,
                "amount": 3500,
            }
        ]
    )

    from webapp.services.sync_service import _write_etf_data

    count = _write_etf_data(db, df, "daily")
    assert count == 1

    rows = db.query(EtfDailyBar).all()
    assert len(rows) == 1
    assert rows[0].sec_code == "510300.SH"
    assert rows[0].close == pytest.approx(3.53)
