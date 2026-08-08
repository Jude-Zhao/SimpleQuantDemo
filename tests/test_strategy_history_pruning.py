"""Tests for strategy history pruning (max_history_runs)."""

from __future__ import annotations

import pytest

from webapp.models.database import Base
from webapp.models.strategy_run import StrategyRun
from webapp.services.strategy_service import _prune_history_runs


@pytest.fixture()
def db():
    """In-memory SQLite session with schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _insert_runs(db, count: int) -> list[int]:
    """Insert ``count`` run records and return their ids in insertion order."""
    ids = []
    for i in range(count):
        run = StrategyRun(
            strategy_type="faa",
            params={},
            status="success",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        ids.append(run.id)
    return ids


def test_prune_keeps_newest_when_over_limit(db):
    ids = _insert_runs(db, 105)
    _prune_history_runs(db, max_runs=100)

    remaining = db.query(StrategyRun).count()
    assert remaining == 100
    # The 5 oldest records are pruned, newest 100 remain.
    committed_ids = {r.id for r in db.query(StrategyRun).all()}
    assert ids[0] not in committed_ids
    assert ids[4] not in committed_ids
    assert ids[5] in committed_ids
    assert ids[-1] in committed_ids


def test_prune_noop_when_at_or_under_limit(db):
    _insert_runs(db, 100)
    _prune_history_runs(db, max_runs=100)
    assert db.query(StrategyRun).count() == 100

    _prune_history_runs(db, max_runs=200)
    assert db.query(StrategyRun).count() == 100


def test_prune_disabled_when_max_runs_non_positive(db):
    _insert_runs(db, 5)
    _prune_history_runs(db, max_runs=0)
    _prune_history_runs(db, max_runs=-1)
    assert db.query(StrategyRun).count() == 5