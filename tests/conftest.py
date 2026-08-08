"""Test fixtures: build a temporary SQLite database with deterministic dummy data.

The core and research/trading tests read data through ``SqliteDataSource``
instead of CSV files, so we seed a throwaway SQLite DB (schema mirrors the
real ``data/simple_quant.db``) with generated price / macro / universe data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.data import SqliteDataSource

ETF_CODES = [
    "510300.SH",
    "510500.SH",
    "159915.SZ",
    "518880.SH",
    "511010.SH",
    "510050.SH",
    "510880.SH",
    "159901.SZ",
    "510180.SH",
    "159919.SZ",
    "588000.SH",
    "512100.SH",
]

MACRO_COLUMNS = [
    "shibor_3m",
    "fr007",
    "cn_gov_1y",
    "cn_gov_10y",
    "usd_cny",
    "copper",
    "gold",
    "rebar",
    "csi300_pe",
    "csi1000_pe",
    "qvix_300etf",
    "spx",
    "ixic",
    "hsi",
]

TRADE_DATES = pd.bdate_range("2024-01-01", "2026-03-13")


def create_test_db(db_path: Path) -> None:
    """Create a SQLite DB with the real schema and seed dummy data."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE etf_daily_bar (
                id VARCHAR NOT NULL,
                sec_code VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                open FLOAT, high FLOAT, low FLOAT, close FLOAT,
                volume FLOAT, amount FLOAT, source VARCHAR,
                PRIMARY KEY (id)
            );
            CREATE TABLE macro_daily (
                trade_date VARCHAR NOT NULL,
                shibor_3m FLOAT, fr007 FLOAT, cn_gov_1y FLOAT, cn_gov_10y FLOAT,
                usd_cny FLOAT, copper FLOAT, gold FLOAT, rebar FLOAT,
                csi300_pe FLOAT, csi1000_pe FLOAT, qvix_300etf FLOAT,
                spx FLOAT, ixic FLOAT, hsi FLOAT,
                PRIMARY KEY (trade_date)
            );
            CREATE TABLE universe_items (
                id INTEGER NOT NULL,
                sec_code VARCHAR NOT NULL,
                sec_name VARCHAR,
                is_active BOOLEAN,
                added_at DATETIME,
                removed_at DATETIME,
                meta JSON,
                PRIMARY KEY (id)
            );
            """
        )
        _seed_prices(conn)
        _seed_macro(conn)
        _seed_universe(conn)
        conn.commit()


def _seed_prices(conn: sqlite3.Connection) -> None:
    rng = np.random.default_rng(42)
    base = pd.DataFrame({"date": TRADE_DATES})
    rows: list[tuple] = []
    for code in ETF_CODES:
        close = 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, len(TRADE_DATES)))
        for i, trade_date in enumerate(TRADE_DATES):
            c = float(close[i])
            rows.append(
                (
                    f"{code}_{trade_date.date().isoformat()}",
                    code,
                    trade_date.date().isoformat(),
                    round(c * 0.998, 4),
                    round(c * 1.002, 4),
                    round(c * 0.997, 4),
                    round(c, 4),
                    float(100000.0 + rng.integers(0, 10000)),
                    float(1000000.0),
                    "test",
                )
            )
    conn.executemany(
        "INSERT INTO etf_daily_bar "
        "(id, sec_code, trade_date, open, high, low, close, volume, amount, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def _seed_macro(conn: sqlite3.Connection) -> None:
    rng = np.random.default_rng(7)
    rows = []
    for trade_date in TRADE_DATES:
        values = [round(float(v), 6) for v in rng.uniform(1.0, 5.0, len(MACRO_COLUMNS))]
        rows.append((trade_date.strftime("%Y-%m-%d"), *values))
    placeholders = ", ".join(["?"] * (1 + len(MACRO_COLUMNS)))
    conn.executemany(
        f"INSERT INTO macro_daily (trade_date, {', '.join(MACRO_COLUMNS)}) VALUES ({placeholders})",
        rows,
    )


def _seed_universe(conn: sqlite3.Connection) -> None:
    rows = [(i, code, f"ETF-{i}") for i, code in enumerate(ETF_CODES)]
    conn.executemany(
        "INSERT INTO universe_items (id, sec_code, sec_name, is_active) VALUES (?, ?, ?, 1)",
        rows,
    )


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """Return the path to a fresh temporary seeded database."""
    db_path = tmp_path / "test.db"
    create_test_db(db_path)
    return db_path


@pytest.fixture
def sqlite_source(test_db_path: Path) -> SqliteDataSource:
    """Return a SqliteDataSource backed by a temporary seeded database."""
    return SqliteDataSource(test_db_path)