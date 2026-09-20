"""F13: 线程/会话启动失败后任务必须置为终态，不永久占用策略互斥。"""

from __future__ import annotations

import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import webapp.models.database as database_module
from webapp.models.database import Base
from webapp.models.strategy_run import StrategyRun
from webapp.schemas.strategy import StrategyRunRequest
from webapp.services import strategy_service


@pytest.fixture()
def db():
    """In-memory SQLite session with schema."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


class _UnstartableThread:
    """模拟 thread.start() 失败（如操作系统线程配额耗尽）。"""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise RuntimeError("can't start new thread")


class _NoopThread:
    """start() 不执行 worker：互斥只由数据库中的记录状态决定。"""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


def _patch_thread_cls(monkeypatch, thread_cls):
    monkeypatch.setattr(
        strategy_service, "threading", types.SimpleNamespace(Thread=thread_cls)
    )


def test_submit_marks_run_failed_when_thread_start_raises(db, monkeypatch):
    _patch_thread_cls(monkeypatch, _UnstartableThread)

    summary = strategy_service.submit_strategy(
        db, StrategyRunRequest(strategy_type="faa")
    )

    assert summary.status == "failed"
    assert summary.error_msg and "线程启动失败" in summary.error_msg
    run = db.query(StrategyRun).filter(StrategyRun.id == summary.run_id).first()
    assert run is not None
    assert run.status == "failed"
    assert run.error_msg and "线程启动失败" in run.error_msg
    assert run.completed_at is not None


def test_submit_allows_resubmission_after_start_failure(db, monkeypatch):
    _patch_thread_cls(monkeypatch, _UnstartableThread)
    first = strategy_service.submit_strategy(
        db, StrategyRunRequest(strategy_type="faa")
    )
    assert first.status == "failed"

    _patch_thread_cls(monkeypatch, _NoopThread)
    second = strategy_service.submit_strategy(
        db, StrategyRunRequest(strategy_type="faa")
    )
    assert second.status == "pending"


def _make_pending_run(engine) -> int:
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSession()
    try:
        run = StrategyRun(strategy_type="faa", params={}, status="pending")
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def test_worker_session_failure_marks_run_failed(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'f13.db'}")
    Base.metadata.create_all(bind=engine)
    run_id = _make_pending_run(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    calls = {"n": 0}

    def flaky_sessionlocal():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection pool exhausted")
        return TestingSession()

    monkeypatch.setattr(database_module, "SessionLocal", flaky_sessionlocal)

    # 同步调用 worker：首个会话创建失败，标记终态走第二个会话
    strategy_service._execute_run(run_id)

    db = TestingSession()
    try:
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        assert run is not None
        assert run.status == "failed"
        assert run.error_msg and "创建数据库会话失败" in run.error_msg
        assert run.completed_at is not None
    finally:
        db.close()
    assert calls["n"] == 2


def test_worker_session_failure_survives_unavailable_database(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'f13b.db'}")
    Base.metadata.create_all(bind=engine)
    run_id = _make_pending_run(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def broken_sessionlocal():
        raise RuntimeError("database down")

    monkeypatch.setattr(database_module, "SessionLocal", broken_sessionlocal)

    # 数据库完全不可用时不得抛异常：worker 线程内无处上报，只能放弃
    strategy_service._execute_run(run_id)

    db = TestingSession()
    try:
        run = db.query(StrategyRun).filter(StrategyRun.id == run_id).first()
        assert run is not None
        assert run.status == "pending"
    finally:
        db.close()
