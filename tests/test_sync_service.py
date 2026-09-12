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
    _write_etf_data(db, df)
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


def test_run_etf_sync_jump_reject_keeps_old_data(db, monkeypatch):
    """校验剔除部分行：默认中止该标的替换并保留原数据。"""
    _seed_old_bar(db, "A.SH", "2024-01-02", close=11.0)

    df = pd.DataFrame(
        [
            {"date": pd.Timestamp("2024-01-02"), "sec": "A.SH", "open": 10, "high": 10, "low": 10, "close": 10.0, "volume": 1, "amount": 1},
            {"date": pd.Timestamp("2024-01-03"), "sec": "A.SH", "open": 5, "high": 5, "low": 5, "close": 5.0, "volume": 1, "amount": 1},  # -50% cliff
        ]
    )
    task = _run_inline_sync(db, monkeypatch, ["A.SH"], _FakeSource(df=df))

    assert task.result["success_count"] == 0
    assert task.result["rejected_count"] == 1
    assert any("跳变校验" in r for r in task.result["rejected_codes"])
    rows = _sec_rows(db, "A.SH")
    assert [r.close for r in rows] == [11.0]


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


# ── BUG-05: 禁止未复权行情混入后复权链路 ───────────────────────────────


def test_write_etf_data_adj_factor_validation(db):
    """非法/缺失 adj_factor 按显式缺失（NULL）处理；合法值原样保存。"""
    from webapp.models.market_data import EtfDailyBar

    from webapp.services.sync_service import _write_etf_data

    base = {"sec": "A.SH", "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1, "amount": 1}
    values = [1.5, 0, -2.0, float("inf"), "abc", float("nan")]
    df = pd.DataFrame(
        [
            {**base, "date": pd.Timestamp("2024-01-0%d" % (i + 2)), "adj_factor": v}
            for i, v in enumerate(values)
        ]
    )
    _write_etf_data(db, df)

    rows = db.query(EtfDailyBar).order_by(EtfDailyBar.trade_date).all()
    assert len(rows) == 6
    assert rows[0].adj_factor == pytest.approx(1.5)
    assert all(r.adj_factor is None for r in rows[1:])  # 0/负/inf/非法/NaN → NULL


def test_run_etf_sync_warns_on_missing_adj_factor(db, monkeypatch):
    """无 adj_factor 列的来源 → 写入成功但产生显式告警。"""
    df = _new_rows_df("A.SH", ["2024-01-02"])  # _new_rows_df 不含 adj_factor 列

    task = _run_inline_sync(db, monkeypatch, ["A.SH"], _FakeSource(df=df))

    assert task.result["success_count"] == 1
    reasons = [str(w.get("reason", "")) for w in task.result["warnings"]]
    assert any("adj_factor" in r for r in reasons)
