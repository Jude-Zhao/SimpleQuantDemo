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


def test_write_etf_data_adj_factor(db):
    """adj_factor column is persisted; NaN becomes NULL."""
    from webapp.models.market_data import EtfDailyBar

    from webapp.services.sync_service import _write_etf_data

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
                "adj_factor": 1.5,
            },
            {
                "date": pd.Timestamp("2024-01-03"),
                "sec": "510300.SH",
                "open": 3.6,
                "high": 3.65,
                "low": 3.55,
                "close": 3.6,
                "volume": 1000,
                "amount": 3500,
                "adj_factor": float("nan"),
            },
        ]
    )
    _write_etf_data(db, df, "daily")
    rows = db.query(EtfDailyBar).order_by(EtfDailyBar.trade_date).all()
    assert rows[0].adj_factor == pytest.approx(1.5)
    assert rows[1].adj_factor is None


def test_filter_jump_anomalies_blocks_unadjusted_cliff():
    """Unadjusted data (no adj_factor) with a >15% cliff row is dropped."""
    from webapp.services.sync_service import _filter_jump_anomalies

    df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-04-20"), "sec": "513660.SH", "close": 3.128},
            {"date": pd.Timestamp("2026-04-21"), "sec": "513660.SH", "close": 1.569},
            {"date": pd.Timestamp("2026-04-22"), "sec": "513660.SH", "close": 1.554},
        ]
    )
    filtered, warnings = _filter_jump_anomalies(df, threshold=15.0)
    assert len(filtered) == 2
    assert len(warnings) == 1
    assert warnings[0]["date"] == "2026-04-21"
    assert filtered["close"].tolist() == [3.128, 1.554]


def test_filter_jump_anomalies_keeps_adjusted_data():
    """hfq data (with adj_factor) is trusted: real >15% moves are kept."""
    from webapp.services.sync_service import _filter_jump_anomalies

    df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-09-30"), "sec": "159915.SZ", "close": 2.232, "adj_factor": 1.0},
            {"date": pd.Timestamp("2024-10-08"), "sec": "159915.SZ", "close": 2.678, "adj_factor": 1.0},
            {"date": pd.Timestamp("2024-10-09"), "sec": "159915.SZ", "close": 2.242, "adj_factor": 1.0},
        ]
    )
    filtered, warnings = _filter_jump_anomalies(df, threshold=15.0)
    assert len(filtered) == 3
    assert warnings == []
