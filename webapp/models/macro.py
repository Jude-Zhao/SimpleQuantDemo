"""Macro data ORM models (daily + monthly tables)."""

from __future__ import annotations

from sqlalchemy import Column, Date, Float, Index, String

from webapp.models.database import Base


class MacroDaily(Base):
    """Daily macro factor data table (wide table, one row per date)."""

    __tablename__ = "macro_daily"

    trade_date = Column(String, primary_key=True)  # YYYY-MM-DD as string for simplicity

    # ── Interest rates ──────────────────────────────────────
    shibor_3m = Column(Float)       # Shibor 3M (%)
    fr007 = Column(Float)           # FR007 repo rate (%)
    cn_gov_1y = Column(Float)       # 1Y gov bond yield (%)
    cn_gov_10y = Column(Float)      # 10Y gov bond yield (%)

    # ── FX ───────────────────────────────────────────────────
    usd_cny = Column(Float)         # USD/CNY central parity

    # ── Commodities ──────────────────────────────────────────
    copper = Column(Float)          # SHFE copper main contract close (yuan/ton)
    gold = Column(Float)            # SHFE gold main contract close (yuan/gram)
    rebar = Column(Float)           # SHFE rebar main contract close (yuan/ton)

    # ── Equity valuation / vol ──────────────────────────────
    csi300_pe = Column(Float)       # CSI 300 rolling PE
    csi1000_pe = Column(Float)      # CSI 1000 rolling PE
    qvix_300etf = Column(Float)     # 300ETF option VIX

    # ── Overseas markets ────────────────────────────────────
    spx = Column(Float)             # S&P 500 close
    ixic = Column(Float)            # Nasdaq close
    hsi = Column(Float)             # Hang Seng close


class MacroMonthly(Base):
    """Monthly macro economic data table (wide table, one row per month)."""

    __tablename__ = "macro_monthly"

    trade_month = Column(String, primary_key=True)  # YYYY-MM

    # ── Money supply ────────────────────────────────────────
    m2_yoy = Column(Float)          # M2 YoY (%)
    m1_yoy = Column(Float)          # M1 YoY (%)
    m1_m2_scissors = Column(Float)  # M1-M2 scissors diff (pct points, derived)

    # ── Economic indicators ─────────────────────────────────
    cpi_yoy = Column(Float)         # CPI YoY (%)
    ppi_yoy = Column(Float)         # PPI YoY (%)
    aggregate_financing = Column(Float)  # Aggregate financing (100M yuan)
