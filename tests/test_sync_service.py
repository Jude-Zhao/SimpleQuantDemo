"""Tests for sync service (ETF market data sync)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from core.data.akshare_source import TencentSourceError
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
    from sqlalchemy.pool import StaticPool

    from webapp.models.database import Base

    # StaticPool 让所有连接共享同一内存库，后台 worker 会话也能看到数据
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_sync_activity():
    """A10: 每个测试后清空活动登记，避免跨测试泄漏。"""
    from webapp.services import sync_service as ss

    yield
    ss._active_requests.clear()


def test_create_and_get_task():
    task = create_task(SyncType.ETF_DAILY)
    assert task.task_id
    assert task.status == SyncStatus.PENDING
    assert get_task(task.task_id) is task
    assert get_task("nonexistent") is None


def test_task_fields_update():
    task = create_task(SyncType.ETF_DAILY)
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
    # mock worker 不会执行 finally 释放，显式释放活动登记
    from webapp.services.sync_service import release_sync_activity

    release_sync_activity(task.task_id)


def test_start_etf_sync_loads_universe_when_no_codes(db):
    """When sec_codes is None, the active universe should be loaded via db."""
    fake_universe = [
        {"sec_code": "510300.SH", "sec_name": "沪深300ETF", "category": "宽基"},
        {"sec_code": "159915.SZ", "sec_name": "创业板ETF", "category": "宽基"},
        {"sec_code": "513660.SH", "sec_name": "恒生ETF", "category": ""},
    ]

    with (
        patch("webapp.services.sync_service._run_etf_sync"),
        patch(
            "webapp.services.sync_service.get_etf_list",
            return_value=fake_universe,
        ) as mock_get_etf_list,
    ):
        task = start_etf_sync(
            db=db,
            start_date="2024-01-02",
            end_date="2024-01-10",
        )
        # get_etf_list must be called with the db session so it returns the universe
        mock_get_etf_list.assert_called_once_with(db)
        assert task.total == 3
    # mock worker 不会执行 finally 释放，显式释放活动登记
    from webapp.services.sync_service import release_sync_activity

    release_sync_activity(task.task_id)


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


def test_collect_jump_warnings_warns_on_jump():
    """断崖校验降级为告警：超阈值行不剔除，warnings 记录明细。"""
    from webapp.services.sync_service import _collect_jump_warnings

    df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2026-04-20"), "sec": "513660.SH", "close": 3.128},
            {"date": pd.Timestamp("2026-04-21"), "sec": "513660.SH", "close": 1.569},
            {"date": pd.Timestamp("2026-04-22"), "sec": "513660.SH", "close": 1.554},
        ]
    )
    result, warnings = _collect_jump_warnings(df, threshold=15.0)
    assert len(result) == 3  # 行数不变，不剔除
    assert len(warnings) == 1
    assert warnings[0]["date"] == "2026-04-21"


# ── BUG-02: 同步失败不得删除旧数据 ─────────────────────────────────────


class _FakeSource:
    """Test double for the primary data source."""

    def __init__(self, df: pd.DataFrame | None = None, error: Exception | None = None):
        self._df = df
        self._error = error

    def get_etf_price_by_codes(self, sec_codes, start_date, end_date, period="daily"):
        if self._error is not None:
            raise self._error
        return self._df


def _seed_old_bar(db, sec: str, date_str: str, close: float = 10.0) -> None:
    from webapp.models.market_data import EtfDailyBar

    db.add(
        EtfDailyBar(
            id=f"{sec}_{date_str}",
            sec_code=sec,
            trade_date=pd.Timestamp(date_str).date(),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=100.0,
            amount=1000.0,
            source="old",
        )
    )
    db.commit()


def _sec_rows(db, sec: str):
    from webapp.models.market_data import EtfDailyBar

    return (
        db.query(EtfDailyBar)
        .filter(EtfDailyBar.sec_code == sec)
        .order_by(EtfDailyBar.trade_date)
        .all()
    )


def _new_rows_df(sec: str, dates: list[str], close: float = 20.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": pd.Timestamp(d),
                "sec": sec,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 100.0,
                "amount": 1000.0,
            }
            for d in dates
        ]
    )


def _run_inline_sync(
    db,
    monkeypatch,
    codes,
    primary,
    start="2024-01-01",
    end="2024-12-31",
):
    """Run _run_etf_sync synchronously against the fixture engine."""
    from sqlalchemy.orm import sessionmaker

    import webapp.models.database as db_mod
    from webapp.services import sync_service as ss

    worker_session = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    monkeypatch.setattr(db_mod, "SessionLocal", worker_session)
    monkeypatch.setattr(ss, "_get_primary_source", lambda: primary)

    task = ss.create_task(SyncType.ETF_DAILY)
    task.total = len(codes)
    ss._run_etf_sync(task.task_id, list(codes), start, end)
    return task


def test_run_etf_sync_source_error_keeps_old_data(db, monkeypatch):
    """源异常：原数据逐行保留，不进入删除步骤。"""
    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)
    _seed_old_bar(db, "A.SH", "2024-01-03", close=12.0)

    task = _run_inline_sync(
        db,
        monkeypatch,
        ["A.SH"],
        _FakeSource(error=RuntimeError("network down")),
    )

    assert task.status == SyncStatus.COMPLETED
    assert task.result["success_count"] == 0
    assert task.result["failed_count"] == 1
    rows = _sec_rows(db, "A.SH")
    assert [r.close for r in rows] == [11.0, 12.0]


def test_run_etf_sync_empty_result_keeps_old_data(db, monkeypatch):
    """空返回：原数据逐行保留。"""
    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)

    task = _run_inline_sync(db, monkeypatch, ["A.SH"], _FakeSource(df=pd.DataFrame()))

    assert task.result["success_count"] == 0
    rows = _sec_rows(db, "A.SH")
    assert len(rows) == 1 and rows[0].close == 11.0


def test_run_etf_sync_midwrite_error_rolls_back_keeps_old(db, monkeypatch):
    """插入中途异常（单标的）：回滚，原数据逐行保留，无新行。"""
    from webapp.services import sync_service as ss

    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)

    real_bar = ss.EtfDailyBar
    calls = {"n": 0}

    class _ExplodingBar:
        def __init__(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("boom")
            self._bar = real_bar(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._bar, name)

    monkeypatch.setattr(ss, "EtfDailyBar", _ExplodingBar)
    df = _new_rows_df("A.SH", ["2024-01-02", "2024-01-03"])

    task = _run_inline_sync(db, monkeypatch, ["A.SH"], _FakeSource(df=df))

    assert task.result["success_count"] == 0
    assert task.result["failed_count"] == 1
    rows = _sec_rows(db, "A.SH")
    assert [r.close for r in rows] == [11.0]
    assert rows[0].source == "old"


def test_run_etf_sync_a_failure_does_not_block_b(db, monkeypatch):
    """A 失败不阻止 B 成功；失败后 Session 无 pending 污染。"""

    class _PerSymbolSource:
        """按标的返回：A 抛错，B 返回 1 行。"""

        def __init__(self, df_by_sec):
            self._df_by_sec = df_by_sec

        def get_etf_price_by_codes(self, sec_codes, start_date, end_date, period="daily"):
            out = []
            for sec in sec_codes:
                df = self._df_by_sec[sec]
                if isinstance(df, Exception):
                    raise df
                if df is not None and not df.empty:
                    out.append(df)
            if not out:
                return pd.DataFrame()
            return pd.concat(out, ignore_index=True)

    df_a = _new_rows_df("A.SH", ["2024-01-02", "2024-01-03"])
    df_b = _new_rows_df("B.SH", ["2024-01-02"])
    source = _PerSymbolSource({"A.SH": RuntimeError("source down"), "B.SH": df_b})

    task = _run_inline_sync(db, monkeypatch, ["A.SH", "B.SH"], source)

    assert task.result["success_count"] == 1
    assert task.result["failed_count"] == 1
    assert _sec_rows(db, "A.SH") == []
    rows_b = _sec_rows(db, "B.SH")
    assert len(rows_b) == 1 and rows_b[0].close == 20.0


def test_run_etf_sync_single_commit_per_symbol(db, monkeypatch):
    """删除与写入落在同一次 commit（无独立提交删除的中间空档）。"""
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker

    import webapp.models.database as db_mod
    from webapp.services import sync_service as ss

    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)

    commits: list[int] = []

    def _after_commit(session):
        commits.append(1)

    WorkerSession = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    event.listen(WorkerSession, "after_commit", _after_commit)
    try:
        monkeypatch.setattr(db_mod, "SessionLocal", WorkerSession)
        df = _new_rows_df("A.SH", ["2024-01-02", "2024-01-03"])
        monkeypatch.setattr(ss, "_get_primary_source", lambda: _FakeSource(df=df))

        task = ss.create_task(SyncType.ETF_DAILY)
        task.total = 1
        ss._run_etf_sync(task.task_id, ["A.SH"], "2024-01-01", "2024-12-31")
    finally:
        event.remove(WorkerSession, "after_commit", _after_commit)

    assert task.result["success_count"] == 1
    assert commits == [1], "成功替换应恰有一次 commit"


def test_run_etf_sync_jump_warns_and_writes(db, monkeypatch):
    """跳变仅告警：同数据成功写入，warnings 出现，无 rejected 键。"""
    df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-02"), "sec": "A.SH", "open": 10, "high": 10, "low": 10, "close": 10.0, "volume": 1, "amount": 1},
            {"date": pd.Timestamp("2024-01-03"), "sec": "A.SH", "open": 5, "high": 5, "low": 5, "close": 5.0, "volume": 1, "amount": 1},  # -50% cliff
        ]
    )
    task = _run_inline_sync(db, monkeypatch, ["A.SH"], _FakeSource(df=df))

    assert task.result["success_count"] == 1
    assert task.result["warning_count"] == 1
    assert "rejected_count" not in task.result
    rows = _sec_rows(db, "A.SH")
    assert [r.close for r in rows] == [10.0, 5.0]


# ── 熔断：连续源级拦截快速止损 ─────────────────────────────────────────


def test_circuit_breaker_skips_remaining_after_consecutive_blocks(db, monkeypatch):
    """连续 3 只源级拦截 → 熔断：源恰被调 3 次，剩余标的记 skipped 未尝试。"""
    calls = {"n": 0}

    class _BlockedSource:
        def get_etf_price_by_codes(self, sec_codes, start_date, end_date, period="daily"):
            calls["n"] += 1
            raise TencentSourceError("腾讯行情接口被 WAF 拦截(HTTP 501)")

    task = _run_inline_sync(
        db, monkeypatch, ["A.SH", "B.SH", "C.SH", "D.SH", "E.SH"], _BlockedSource()
    )

    assert task.status == SyncStatus.COMPLETED
    assert task.result["failed_count"] == 3
    assert task.result["skipped_count"] == 2
    assert task.result["skipped_codes"] == ["D.SH", "E.SH"]
    assert calls["n"] == 3
    assert "熔断跳过" in task.message
    skipped_rows = [r for r in task.result["results"] if r["status"] == "skipped"]
    assert [r["sec_code"] for r in skipped_rows] == ["D.SH", "E.SH"]


def test_circuit_breaker_counter_resets_after_success(db, monkeypatch):
    """拦截×2 + 成功 + 拦截×2 → 计数清零不熔断，全部标的均被尝试。"""
    outcomes = ["block", "block", "ok", "block", "block"]

    class _FlakySource:
        def get_etf_price_by_codes(self, sec_codes, start_date, end_date, period="daily"):
            outcome = outcomes.pop(0)
            if outcome == "block":
                raise TencentSourceError("腾讯行情接口被 WAF 拦截(HTTP 501)")
            return _new_rows_df(sec_codes[0], ["2024-01-02"])

    task = _run_inline_sync(
        db, monkeypatch, ["A.SH", "B.SH", "C.SH", "D.SH", "E.SH"], _FlakySource()
    )

    assert task.result["success_count"] == 1
    assert task.result["failed_count"] == 4
    assert task.result["skipped_count"] == 0


def test_circuit_breaker_ignores_non_block_errors(db, monkeypatch):
    """普通异常连续失败不熔断（计数仅对 TencentSourceError），全部尝试。"""

    class _FailingSource:
        def get_etf_price_by_codes(self, sec_codes, start_date, end_date, period="daily"):
            raise RuntimeError("network down")

    task = _run_inline_sync(db, monkeypatch, ["A.SH", "B.SH", "C.SH", "D.SH"], _FailingSource())

    assert task.result["failed_count"] == 4
    assert task.result["skipped_count"] == 0


def test_run_etf_sync_success_replaces_range_outside_untouched(db, monkeypatch):
    """成功替换：区间内无重复键、按新数据替换；区间外记录不变。"""
    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)
    _seed_old_bar(db, "A.SH", "2023-12-29", close=9.0)  # 区间外

    df = _new_rows_df("A.SH", ["2024-01-02", "2024-01-03"])
    task = _run_inline_sync(
        db, monkeypatch, ["A.SH"], _FakeSource(df=df),
        start="2024-01-01", end="2024-12-31",
    )

    assert task.result["success_count"] == 1
    assert task.result["total_rows"] == 2
    rows = _sec_rows(db, "A.SH")
    closes = [r.close for r in rows]
    assert sorted(closes) == [9.0, 20.0, 20.0]
    # 无重复键
    keys = [(r.sec_code, r.trade_date) for r in rows]
    assert len(keys) == len(set(keys))
    # 区间外记录保留旧值
    outside = [r for r in rows if r.trade_date == pd.Timestamp("2023-12-29").date()]
    assert len(outside) == 1 and outside[0].close == 9.0


# ── F01: 非空但不完整的源响应不得删除旧数据 ───────────────────────────


def test_partial_source_response_keeps_old_rows(db, monkeypatch):
    """F01：源只返回区间中段 1 天时，其余旧日期必须原样保留，中段更新。"""
    _seed_old_bar(db, "A.SH", "2024-01-02", close=10.0)
    _seed_old_bar(db, "A.SH", "2024-01-03", close=10.0)
    _seed_old_bar(db, "A.SH", "2024-01-04", close=10.0)

    df = _new_rows_df("A.SH", ["2024-01-03"], close=20.0)
    task = _run_inline_sync(
        db, monkeypatch, ["A.SH"], _FakeSource(df=df),
        start="2024-01-01", end="2024-01-31",
    )

    assert task.result["success_count"] == 1
    rows = _sec_rows(db, "A.SH")
    assert [(str(r.trade_date), r.close) for r in rows] == [
        ("2024-01-02", 10.0),  # 源遗漏日保留旧行
        ("2024-01-03", 20.0),  # 收到的新值覆盖同键旧值
        ("2024-01-04", 10.0),  # 源遗漏日保留旧行
    ]


def test_replace_etf_range_warns_on_omitted_dates(db, caplog):
    """F01：源响应遗漏区间内已有日期时告警（旧行保留，显式可观测）。"""
    import logging

    from webapp.services.sync_service import _replace_etf_range

    _seed_old_bar(db, "A.SH", "2024-01-02", close=10.0)
    df = _new_rows_df("A.SH", ["2024-01-03"], close=20.0)

    with caplog.at_level(logging.WARNING, logger="webapp.services.sync_service"):
        _replace_etf_range(db, df, "A.SH", "2024-01-01", "2024-01-31")

    rows = _sec_rows(db, "A.SH")
    assert [(str(r.trade_date), r.close) for r in rows] == [
        ("2024-01-02", 10.0),
        ("2024-01-03", 20.0),
    ]
    assert any("遗漏请求区间内已有日期" in r.getMessage() for r in caplog.records)


def test_replace_etf_range_drops_out_of_range_rows(db, caplog):
    """轻校验：请求区间之外的行丢弃并告警，不写库。"""
    import logging

    from webapp.services.sync_service import _replace_etf_range

    df = _new_rows_df("A.SH", ["2024-01-03", "2023-06-01"], close=20.0)

    with caplog.at_level(logging.WARNING, logger="webapp.services.sync_service"):
        written = _replace_etf_range(db, df, "A.SH", "2024-01-01", "2024-01-31")

    assert written == 1
    rows = _sec_rows(db, "A.SH")
    assert [str(r.trade_date) for r in rows] == ["2024-01-03"]
    assert any("请求区间之外" in r.getMessage() for r in caplog.records)


def test_successful_sync_records_coverage(db, monkeypatch):
    """F03：同步成功后必须记录源覆盖元数据（供缓存判定免拉）。"""
    from webapp.models.market_data import EtfCacheCoverage

    df = _new_rows_df("A.SH", ["2024-01-02", "2024-01-03"])
    task = _run_inline_sync(
        db, monkeypatch, ["A.SH"], _FakeSource(df=df),
        start="2024-01-01", end="2024-01-31",
    )

    assert task.result["success_count"] == 1
    row = db.get(EtfCacheCoverage, "A.SH")
    assert row is not None
    assert str(row.fetched_from) == "2024-01-01"
    assert str(row.fetched_to) == "2024-01-31"
