"""Strategy run record ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from webapp.models.database import Base


class StrategyRun(Base):
    """A single strategy execution record.

    Stores the strategy type, parameters, universe snapshot, date range,
    result summary, and status for every run.
    """

    __tablename__ = "strategy_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_type = Column(String, nullable=False, index=True)  # linear_factor / mvo / bl
    params = Column(JSON, default=dict)
    universe_snapshot = Column(JSON, default=list)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    result_summary = Column(JSON, default=dict)  # nav_series, metrics, weights, constraint_violations
    status = Column(String, default="pending", index=True)  # pending / running / success / failed
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)