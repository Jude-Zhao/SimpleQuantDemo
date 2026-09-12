"""Tests for data_service."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from webapp.models.database import Base
from webapp.services.data_service import (
    get_etf_list,
    get_etf_price,
)


@pytest.fixture
def test_db():
    """In-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_get_etf_list(test_db):
    """get_etf_list returns the active universe from the DB."""
    from webapp.models.universe import UniverseItem

    for code, name in [
        ("510300.SH", "沪深300ETF"),
        ("510500.SH", "中证500ETF"),
        ("159915.SZ", "创业板ETF"),
    ]:
        test_db.add(UniverseItem(sec_code=code, sec_name=name))
    test_db.commit()

    etfs = get_etf_list(test_db)
    assert isinstance(etfs, list)
    assert len(etfs) == 3
    codes = [e["sec_code"] for e in etfs]
    assert "510300.SH" in codes
    assert "510500.SH" in codes
    assert "159915.SZ" in codes
    # Each entry has required fields
    for e in etfs:
        assert "sec_code" in e
        assert "sec_name" in e
        assert "category" in e

    # Without a DB session it returns an empty list.
    assert get_etf_list() == []


def test_get_etf_price_uses_cache(test_db):
    """Verify cache writer stores data and cache reader retrieves it."""
    from webapp.services.data_service import _cache_reader, _cache_writer

    # Write some test data
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    rows = []
    for d in dates:
        for sec in ["510300.SH", "510500.SH"]:
            rows.append({
                "date": d,
                "sec": sec,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000000.0,
                "amount": 100000000.0,
                "source": "test",
            })
    df = pd.DataFrame(rows)

    writer = _cache_writer(test_db)
    writer(df, "daily")

    # Read it back
    reader = _cache_reader(test_db)
    cached = reader(["510300.SH"], "2024-01-02", "2024-01-04", "daily")

    assert not cached.empty
    assert len(cached) == 3
    assert cached["sec"].unique()[0] == "510300.SH"
    assert "close" in cached.columns


def test_get_etf_price_minute_cache(test_db):
    """Verify minute bar cache works."""
    from webapp.services.data_service import _cache_reader, _cache_writer

    times = pd.date_range("2024-01-02 09:30", periods=5, freq="5min")
    rows = []
    for t in times:
        rows.append({
            "date": t,
            "sec": "510300.SH",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10000.0,
            "amount": 1000000.0,
            "source": "test",
        })
    df = pd.DataFrame(rows)

    writer = _cache_writer(test_db)
    writer(df, "5m")

    reader = _cache_reader(test_db)
    cached = reader(["510300.SH"], "2024-01-02 09:30", "2024-01-02 10:00", "5m")

    assert not cached.empty
    assert len(cached) == 5


def test_cache_idempotent(test_db):
    """Writing the same data twice should not create duplicates."""
    from webapp.services.data_service import _cache_reader, _cache_writer

    dates = pd.date_range("2024-01-02", periods=2, freq="B")
    rows = [
        {"date": d, "sec": "510300.SH", "open": 100, "high": 101, "low": 99,
         "close": 100.5, "volume": 1000, "amount": 100000, "source": "test"}
        for d in dates
    ]
    df = pd.DataFrame(rows)

    writer = _cache_writer(test_db)
    writer(df, "daily")
    writer(df, "daily")  # Write again

    reader = _cache_reader(test_db)
    cached = reader(["510300.SH"], "2024-01-02", "2024-01-03", "daily")
    assert len(cached) == 2  # Not 4


# ── BUG-05: 两个写入入口准确保存 adj_factor，读回对称 ───────────────────


def test_daily_cache_adj_factor_roundtrip(test_db):
    """adj_factor 合法值保存、非法/缺失按 NULL；读回含 adj_factor 列。"""
    from webapp.services.data_service import _cache_reader, _cache_writer

    dates = list(pd.date_range("2024-01-02", periods=3, freq="B"))
    factors = [1.5, float("nan"), -2.0]  # 合法 / 缺失 / 非法
    rows = [
        {
            "date": d,
            "sec": "510300.SH",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100.5,
            "volume": 1000,
            "amount": 100000,
            "adj_factor": af,
            "source": "test",
        }
        for d, af in zip(dates, factors)
    ]
    _cache_writer(test_db)(pd.DataFrame(rows), "daily")

    cached = _cache_reader(test_db)(["510300.SH"], "2024-01-02", "2024-01-04", "daily")
    assert not cached.empty
    assert "adj_factor" in cached.columns  # 读写字段对称
    saved = cached.sort_values("date")["adj_factor"].tolist()
    assert saved[0] == pytest.approx(1.5)
    assert saved[1] is None or pd.isna(saved[1])
    assert saved[2] is None or pd.isna(saved[2])


def test_daily_cache_without_adj_factor_stores_null(test_db):
    """无 adj_factor 列的数据（baostock 路径）保存后为 NULL，不默认造 1。"""
    from webapp.models.market_data import EtfDailyBar
    from webapp.services.data_service import _cache_writer

    rows = [
        {"date": pd.Timestamp("2024-01-02"), "sec": "510300.SH", "open": 100,
         "high": 101, "low": 99, "close": 100.5, "volume": 1000, "amount": 100000,
         "source": "baostock"}
    ]
    _cache_writer(test_db)(pd.DataFrame(rows), "daily")

    bar = test_db.query(EtfDailyBar).filter_by(sec_code="510300.SH").one()
    assert bar.adj_factor is None


# ── BUG-06: 分钟缓存半开区间 ───────────────────────────────────────────


def test_minute_cache_half_open_end(test_db):
    """结束日最后一根 bar（15:00）在【开始日零点, 结束日下一日零点）内可查。"""
    from webapp.services.data_service import _cache_reader, _cache_writer

    times = pd.to_datetime(
        ["2024-01-02 09:35:00", "2024-01-02 15:00:00", "2024-01-03 09:35:00"]
    )
    rows = [
        {"date": t, "sec": "510300.SH", "open": 1, "high": 1, "low": 1,
         "close": 1.0, "volume": 1, "amount": 1, "source": "test"}
        for t in times
    ]
    _cache_writer(test_db)(pd.DataFrame(rows), "5m")

    reader = _cache_reader(test_db)
    out = reader(["510300.SH"], "2024-01-02", "2024-01-02", "5m")
    assert len(out) == 2  # 结束日 15:00 的 bar 可查

    out2 = reader(["510300.SH"], "2024-01-02", "2024-01-03", "5m")
    assert len(out2) == 3

    out3 = reader(["510300.SH"], "2024-01-01", "2024-01-01", "5m")
    assert out3.empty  # 开始日之前无数据
