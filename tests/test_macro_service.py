"""Tests for macro data service layer (no network, uses in-memory DB)."""

from __future__ import annotations

import pandas as pd
import pytest

from webapp.models.database import Base, SessionLocal
from webapp.services.macro_service import (
    DAILY_FIELDS,
    MONTHLY_FIELDS,
    get_daily_macro,
    get_monthly_macro,
    list_fields,
)


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


def test_list_fields_daily():
    fields = list_fields("daily")
    assert len(fields) == len(DAILY_FIELDS)
    names = {f["name"] for f in fields}
    assert {"shibor_3m", "cn_gov_10y", "spx", "hsi"}.issubset(names)


def test_list_fields_monthly():
    fields = list_fields("monthly")
    assert len(fields) == len(MONTHLY_FIELDS)
    names = {f["name"] for f in fields}
    assert {"m2_yoy", "m1_yoy", "cpi_yoy"}.issubset(names)


def test_list_fields_all():
    fields = list_fields()
    assert len(fields) == len(DAILY_FIELDS) + len(MONTHLY_FIELDS)


def test_get_daily_macro_empty(db):
    df = get_daily_macro(db)
    assert df.empty


def test_get_monthly_macro_empty(db):
    df = get_monthly_macro(db)
    assert df.empty


def test_get_daily_macro_returns_data(db):
    from webapp.models.macro import MacroDaily

    db.add(MacroDaily(trade_date="2024-01-02", shibor_3m=2.45, cn_gov_10y=2.56))
    db.add(MacroDaily(trade_date="2024-01-03", shibor_3m=2.42, cn_gov_10y=2.55))
    db.commit()

    df = get_daily_macro(db)
    assert len(df) == 2
    assert "shibor_3m" in df.columns
    assert df.loc["2024-01-02", "shibor_3m"] == pytest.approx(2.45)

    # Date filter
    df2 = get_daily_macro(db, start_date="2024-01-03")
    assert len(df2) == 1

    # Field filter
    df3 = get_daily_macro(db, fields=["shibor_3m"])
    assert list(df3.columns) == ["shibor_3m"]


def test_get_monthly_macro_returns_data(db):
    from webapp.models.macro import MacroMonthly

    db.add(MacroMonthly(trade_month="2024-01", m2_yoy=8.7, m1_yoy=5.9, cpi_yoy=-0.8))
    db.add(MacroMonthly(trade_month="2024-02", m2_yoy=8.7, m1_yoy=1.2, cpi_yoy=0.7))
    db.commit()

    df = get_monthly_macro(db)
    assert len(df) == 2
    assert "m2_yoy" in df.columns
    assert df.loc["2024-01", "m2_yoy"] == pytest.approx(8.7)

    df2 = get_monthly_macro(db, start_month="2024-02")
    assert len(df2) == 1
