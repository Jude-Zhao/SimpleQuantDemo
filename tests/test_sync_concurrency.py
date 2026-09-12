"""A10: 同步冲突、并发上限与实际写入结果（进程内锁，确定性测试）。

测试使用 threading.Event 阻塞 fake worker 制造确定的并发状态，
finally 释放 event；禁止依赖 sleep 随机时机。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import webapp.models.database as db_mod
from webapp.models.database import Base
from webapp.services import sync_service as ss


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    S = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = S()
    try:
        yield session
    finally:
        session.close()


def _fake_config(max_tasks: int = 2):
    return SimpleNamespace(
        sync=SimpleNamespace(max_concurrent_tasks=max_tasks, default_start_date="2021-01-04"),
        datasource=SimpleNamespace(jump_threshold=15.0),
    )


def _patch_env(db, monkeypatch, max_tasks: int = 2):
    worker_session = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    monkeypatch.setattr(db_mod, "SessionLocal", worker_session)
    monkeypatch.setattr("webapp.config.get_config", lambda: _fake_config(max_tasks))


def _rows(sec: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": pd.Timestamp(d), "sec": sec, "open": 1, "high": 1, "low": 1,
             "close": 10.0, "volume": 1, "amount": 1}
            for d in dates
        ]
    )


class _BlockingSource:
    """阻塞 worker 的 fake 源：entered 标记已进入，release 放行。"""

    def __init__(self, df: pd.DataFrame, release_immediately: bool = False):
        self.df = df
        self.entered = threading.Event()
        self.release = threading.Event()
        if release_immediately:
            self.release.set()

    def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
        self.entered.set()
        assert self.release.wait(timeout=10), "blocking source was not released"
        return self.df[self.df["sec"].isin(sec_codes)].copy()


class _RoutingSource:
    """按 (证券, 起始日, 周期) 路由到对应 fake 源（主/备共用）。"""

    def __init__(self):
        self.routes: dict[tuple[str, str, str], _BlockingSource] = {}

    def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
        key = (sec_codes[0], str(start_date), str(period))
        return self.routes[key].get_etf_price_by_codes(
            sec_codes, start_date, end_date, period
        )


def _wait_terminal(task, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = ss.get_task(task.task_id)
        if t is not None and t.status in (ss.SyncStatus.COMPLETED, ss.SyncStatus.FAILED):
            return t
        time.sleep(0.02)
    raise AssertionError("task did not reach terminal state in time")


@pytest.fixture(autouse=True)
def _clean_activity():
    yield
    ss._active_requests.clear()


def test_overlap_conflict_rejected_then_released(db, monkeypatch):
    """重叠区间 → SyncConflictError；完成后释放，同资源可再次启动。"""
    _patch_env(db, monkeypatch)
    router = _RoutingSource()
    blocker_a = _BlockingSource(_rows("A.SH", ["2024-01-02"]))
    router.routes[("A.SH", "2024-01-01", "daily")] = blocker_a
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    task1 = ss.start_etf_sync(
        db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31"
    )
    try:
        assert blocker_a.entered.wait(timeout=5)
        with pytest.raises(ss.SyncConflictError):
            ss.start_etf_sync(
                db=db, sec_codes=["A.SH"], start_date="2024-06-01", end_date="2024-12-31"
            )
    finally:
        blocker_a.release.set()
    _wait_terminal(task1)

    task2 = ss.start_etf_sync(
        db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31"
    )
    _wait_terminal(task2)


def test_capacity_limit_third_rejected(db, monkeypatch):
    """前两个任务占用容量，第三个满容量拒绝；释放后可再启动。"""
    _patch_env(db, monkeypatch, max_tasks=2)
    router = _RoutingSource()
    blockers = {}
    for sec in ("A.SH", "B.SH", "C.SH"):
        blockers[sec] = _BlockingSource(_rows(sec, ["2024-01-02"]))
        router.routes[(sec, "2024-01-01", "daily")] = blockers[sec]
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    t1 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    t2 = ss.start_etf_sync(db=db, sec_codes=["B.SH"], start_date="2024-01-01", end_date="2024-12-31")
    try:
        assert blockers["A.SH"].entered.wait(timeout=5)
        assert blockers["B.SH"].entered.wait(timeout=5)
        with pytest.raises(ss.SyncCapacityError):
            ss.start_etf_sync(db=db, sec_codes=["C.SH"], start_date="2024-01-01", end_date="2024-12-31")
    finally:
        for b in blockers.values():
            b.release.set()
    _wait_terminal(t1)
    _wait_terminal(t2)

    t3 = ss.start_etf_sync(db=db, sec_codes=["C.SH"], start_date="2024-01-01", end_date="2024-12-31")
    _wait_terminal(t3)


def test_disjoint_ranges_and_different_period_allowed(db, monkeypatch):
    """不重叠区间允许；不同周期资源允许（A daily 与 A 5m 互不冲突）。"""
    _patch_env(db, monkeypatch)
    router = _RoutingSource()
    blocker_first = _BlockingSource(_rows("A.SH", ["2024-01-02"]))
    router.routes[("A.SH", "2024-01-01", "daily")] = blocker_first
    router.routes[("A.SH", "2024-07-01", "daily")] = _BlockingSource(
        _rows("A.SH", ["2024-07-01"]), release_immediately=True
    )
    router.routes[("A.SH", "2024-01-01", "5m")] = _BlockingSource(
        _rows("A.SH", ["2024-01-02"]), release_immediately=True
    )
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    t1 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-06-30")
    try:
        assert blocker_first.entered.wait(timeout=5)
        # 不重叠区间：允许并行
        t2 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-07-01", end_date="2024-12-31")
        t2 = _wait_terminal(t2)
        assert t2.status == ss.SyncStatus.COMPLETED
        # 不同周期：允许并行
        t3 = ss.start_etf_sync(
            db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-06-30", period="5m"
        )
        _wait_terminal(t3)
    finally:
        blocker_first.release.set()
    _wait_terminal(t1)


def test_batch_conflict_rejects_whole_request(db, monkeypatch):
    """批量请求任一资源冲突则整体拒绝：不创建新 task、不拆分静默执行。"""
    _patch_env(db, monkeypatch)
    router = _RoutingSource()
    blocker_a = _BlockingSource(_rows("A.SH", ["2024-01-02"]))
    router.routes[("A.SH", "2024-01-01", "daily")] = blocker_a
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    t1 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    try:
        assert blocker_a.entered.wait(timeout=5)
        tasks_before = set(ss._tasks)
        with pytest.raises(ss.SyncConflictError):
            ss.start_etf_sync(
                db=db, sec_codes=["B.SH", "A.SH"], start_date="2024-01-01", end_date="2024-12-31"
            )
        assert set(ss._tasks) == tasks_before  # 未创建新 task
    finally:
        blocker_a.release.set()
    _wait_terminal(t1)


def test_dedup_codes(db, monkeypatch):
    """证券先去重：重复代码只产生一个资源/一个任务单元。"""
    _patch_env(db, monkeypatch)
    router = _RoutingSource()
    router.routes[("A.SH", "2024-01-01", "daily")] = _BlockingSource(
        _rows("A.SH", ["2024-01-02"]), release_immediately=True
    )
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    task = ss.start_etf_sync(db=db, sec_codes=["A.SH", "A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    assert task.total == 1
    _wait_terminal(task)


def test_worker_exception_releases_activity(db, monkeypatch):
    """worker 异常（源始终失败）→ 任务完成且活动登记释放。"""
    _patch_env(db, monkeypatch)

    class _FailingSource:
        def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
            raise RuntimeError("source down")

    monkeypatch.setattr(ss, "_get_primary_source", lambda: _FailingSource())
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: _FailingSource())

    t1 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    t1 = _wait_terminal(t1)
    assert t1.status == ss.SyncStatus.COMPLETED
    assert t1.result["failed_count"] == 1

    # 活动已释放：同资源可再次启动（不抛冲突）
    t2 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    _wait_terminal(t2)


def test_sessionlocal_failure_releases_activity(db, monkeypatch):
    """D1: SessionLocal() 本身失败（如数据库连接异常）→ 任务标记失败且
    活动登记释放，同资源可再次启动（不再永久 409）。"""
    calls = {"n": 0}

    def _failing_session_local():
        calls["n"] += 1
        raise RuntimeError("db connection failed")

    monkeypatch.setattr(db_mod, "SessionLocal", _failing_session_local)
    monkeypatch.setattr("webapp.config.get_config", lambda: _fake_config(2))
    monkeypatch.setattr(ss, "_get_primary_source", lambda: SimpleNamespace())
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: SimpleNamespace())

    t1 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    t1 = _wait_terminal(t1)
    assert t1.status == ss.SyncStatus.FAILED
    assert calls["n"] == 1

    # 活动登记已释放：恢复正常的 SessionLocal 后同资源可再次启动
    _patch_env(db, monkeypatch)
    t2 = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    _wait_terminal(t2)


def test_written_range_is_actual_not_requested(db, monkeypatch):
    """实际返回范围不复制请求结束日：written_end = 实际 frame 最大日期。"""
    _patch_env(db, monkeypatch)
    router = _RoutingSource()
    # 实际数据只到 01-03，请求到 12-31
    router.routes[("A.SH", "2024-01-01", "daily")] = _BlockingSource(
        _rows("A.SH", ["2024-01-02", "2024-01-03"]), release_immediately=True
    )
    monkeypatch.setattr(ss, "_get_primary_source", lambda: router)
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: router)

    task = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    task = _wait_terminal(task)

    assert task.result["success_count"] == 1
    result = task.result["results"][0]
    assert result["status"] == "success"
    assert result["requested_end"] == "2024-12-31"
    assert result["written_start"] == "2024-01-02"
    assert result["written_end"] == "2024-01-03"
    assert result["rows"] == 2
    assert result["source"] == "primary"


def test_written_empty_on_failure(db, monkeypatch):
    """失败标的 written 为空。"""
    _patch_env(db, monkeypatch)

    class _FailingSource:
        def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
            raise RuntimeError("source down")

    monkeypatch.setattr(ss, "_get_primary_source", lambda: _FailingSource())
    monkeypatch.setattr(ss, "_get_secondary_source", lambda: _FailingSource())

    task = ss.start_etf_sync(db=db, sec_codes=["A.SH"], start_date="2024-01-01", end_date="2024-12-31")
    task = _wait_terminal(task)
    result = task.result["results"][0]
    assert result["status"] == "failed"
    assert result["written_start"] == ""
    assert result["written_end"] == ""
    assert result["rows"] == 0
