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


# ── F04：重叠新行情必须更新旧缓存（upsert），返回与持久化语义一致 ──────


def test_write_daily_cache_upserts_revisions(test_db):
    """F04：新拉取覆盖已有日期时，库内旧值必须被新值替换（原实现跳过已
    存在 id，修订值只进返回不进库）。"""
    from webapp.services.data_service import _cache_reader, _cache_writer

    def _frame(closes):
        return pd.DataFrame(
            [
                {"date": pd.Timestamp("2024-01-0" + str(i + 2)), "sec": "510300.SH",
                 "open": c, "high": c, "low": c, "close": c, "volume": 1, "amount": 1,
                 "source": "test"}
                for i, c in enumerate(closes)
            ]
        )

    writer = _cache_writer(test_db)
    writer(_frame([10.0, 11.0]), "daily")
    writer(_frame([20.0, 21.0, 22.0]), "daily")  # 重叠 + 新日期

    reader = _cache_reader(test_db)
    cached = reader(["510300.SH"], None, None, "daily")
    got = cached.sort_values("date").close.tolist()
    assert got == [20.0, 21.0, 22.0]  # 修订值入库，与返回语义一致


# ── F03：源覆盖元数据（EtfCacheCoverage）读写 ─────────────────────────


def test_coverage_rows_monotone_and_reader(test_db):
    """fetched_from 取历史最小、fetched_to 取历史最大；reader 只返回
    fetched_from <= 请求 start 的证券。"""
    from webapp.services.data_service import (
        _coverage_reader,
        upsert_coverage_rows,
    )

    upsert_coverage_rows(test_db, ["510300.SH", "510500.SH"], "2024-06-01", "2024-12-31")
    upsert_coverage_rows(test_db, ["510300.SH"], "2021-01-04", "2024-05-31")  # 扩张 from

    reader = _coverage_reader(test_db)
    assert reader(["510300.SH", "510500.SH"], "2023-01-01", None, "daily") == {"510300.SH"}
    assert reader(["510300.SH", "510500.SH"], "2024-06-01", None, "daily") == {
        "510300.SH",
        "510500.SH",
    }
    assert reader(["510300.SH"], None, None, "daily") == set()  # 无界请求无 start 语义
