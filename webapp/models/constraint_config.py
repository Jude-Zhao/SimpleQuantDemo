"""Optimization constraints persistence model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer

from webapp.models.database import Base


class ConstraintConfig(Base):
    """Single-row persistence of the optimization constraints.

    The full :class:`OptimizationConstraints` payload is stored as JSON so the
    schema stays flexible. ``init_db``'s ``create_all`` creates the table.
    """

    __tablename__ = "constraint_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)