"""Test fixtures: build a temporary SQLite database with deterministic dummy data.

The core and research/trading tests read data through ``SqliteDataSource``
instead of CSV files, so we seed a throwaway SQLite DB (schema mirrors the
real ``data/simple_quant.db``) with generated price / macro / universe data.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.data import SqliteDataSource
from core.data.default_universe import DEFAULT_ACTIVE_CODES

# ── webapp 测试隔离（BUG-01）──────────────────────────────────────────
# 必须在 pytest 导入任何 webapp 模块之前把 DATABASE_URL 指向临时目录中的测试库：
# webapp.models.database 在首次 import 时固化 engine/SessionLocal/_config，
# 而 conftest.py 先于所有测试模块被导入，因此这里的环境变量能覆盖默认业务库。
_WEBAPP_TMP_DIR = Path(tempfile.mkdtemp(prefix="sq_webapp_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_WEBAPP_TMP_DIR / 'webapp_test.db').as_posix()}"

# 跨测试保留的参考数据表（价格/宏观数据只读）；其余表在每个 webapp 测试前清空重置。
_WEBAPP_KEEP_TABLES = {"etf_daily_bar", "etf_minute_bar", "macro_daily", "macro_monthly"}

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


def _seed_prices(conn: sqlite3.Connection, codes: list[str] | None = None) -> None:
    codes = codes if codes is not None else ETF_CODES
    rng = np.random.default_rng(42)
    base = pd.DataFrame({"date": TRADE_DATES})
    rows: list[tuple] = []
    for code in codes:
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


def seed_webapp_reference_data(db_path: Path) -> None:
    """Seed price/macro reference rows into the ORM-created webapp test DB.

    Price seed covers the default universe plus the extra codes used by
    tests, so cached-source reads never fall through to the (blocked)
    network.
    """
    codes = list(dict.fromkeys([*DEFAULT_ACTIVE_CODES, *ETF_CODES]))
    with sqlite3.connect(db_path) as conn:
        _seed_prices(conn, codes)
        _seed_macro(conn)
        conn.commit()


def _wait_webapp_tasks_quiesce(timeout: float = 20.0) -> None:
    """Wait until strategy/sync/macro background work reaches a terminal state."""
    from webapp.models.database import SessionLocal
    from webapp.models.strategy_run import StrategyRun
    from webapp.services.macro_service import MacroSyncStatus, _macro_tasks
    from webapp.services.sync_service import SyncStatus, _tasks

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            busy = (
                db.query(StrategyRun)
                .filter(StrategyRun.status.in_(["pending", "running"]))
                .first()
                is not None
            )
        finally:
            db.close()
        if not busy:
            busy = any(
                t.status in (SyncStatus.PENDING, SyncStatus.RUNNING)
                for t in list(_tasks.values())
            )
        if not busy:
            busy = any(
                t.status in (MacroSyncStatus.PENDING, MacroSyncStatus.RUNNING)
                for t in list(_macro_tasks.values())
            )
        if not busy:
            return
        time.sleep(0.05)


def _reset_webapp_mutable_state() -> None:
    """Clear rows written by webapp tests (order independence); keep reference data."""
    from webapp.models.database import Base, SessionLocal
    from webapp.services.universe_service import seed_default_universe

    db = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _WEBAPP_KEEP_TABLES:
                continue
            db.execute(table.delete())
        db.commit()
        seed_default_universe(db)
        db.commit()
    finally:
        db.close()

    from webapp.services import macro_service, sync_service

    with sync_service._tasks_lock:
        sync_service._tasks.clear()
    with macro_service._macro_tasks_lock:
        macro_service._macro_tasks.clear()
    # A10: 清空活动同步登记
    sync_service._active_requests.clear()


@pytest.fixture(scope="session")
def webapp_seeded_db() -> Path:
    """Import webapp against the temp DATABASE_URL, init schema, seed reference data."""
    from webapp.main import app  # noqa: F401  (create_app() 执行 init_db + cleanup_orphaned_runs)
    from webapp.models.database import Base, engine, init_db

    init_db()
    Base.metadata.create_all(bind=engine)
    db_path = _WEBAPP_TMP_DIR / "webapp_test.db"
    seed_webapp_reference_data(db_path)
    return db_path


class _OfflineSource:
    """webapp 测试用离线数据源替身（BUG-01）：不做任何网络访问。

    缓存未覆盖时源调用返回空，CachedDataSource 回退合并缓存数据，
    使策略/因子等读路径在禁网下确定性地依赖种子数据。
    """

    _COLUMNS = ["date", "sec", "open", "high", "low", "close", "volume", "amount"]

    def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
        return pd.DataFrame(columns=self._COLUMNS)

    def get_etf_price(self, start_date=None, end_date=None):
        return pd.DataFrame(columns=self._COLUMNS)

    def get_universe(self):
        return list(DEFAULT_ACTIVE_CODES)

    def get_macro_factors(self, start_date=None, end_date=None, trading_dates=None):
        return pd.DataFrame()


@pytest.fixture()
def webapp_clean_state(webapp_seeded_db, monkeypatch):
    """每个 webapp 测试前：等待后台任务结束、清空可变表、复位任务内存状态，
    并将数据源替换为离线替身（源调用确定性返回空）。"""
    _wait_webapp_tasks_quiesce()
    _reset_webapp_mutable_state()
    from webapp.services import data_service as _ds

    monkeypatch.setattr(_ds, "_get_primary_source", lambda: _OfflineSource())
    monkeypatch.setattr(_ds, "_get_secondary_source", lambda: _OfflineSource())
    yield


@pytest.fixture(autouse=True)
def _no_network():
    """阻断所有测试的外部网络连接（BUG-01）；数据源用例必须使用测试替身/固定数据。

    允许回环地址连接——Windows 上 asyncio Proactor 事件循环的 socketpair
    需要向 127.0.0.1 发起真实 connect；其余目标一律拒绝。
    """
    import ipaddress
    import socket

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def _loopback(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return True
        host = address[0]
        if host in ("", "localhost"):
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return ip.is_loopback or ip.is_unspecified

    def _blocked_connect(self, address):
        if _loopback(address):
            return orig_connect(self, address)
        raise AssertionError(
            f"测试环境中禁止外部网络连接（BUG-01 隔离）：{address!r}；请使用测试替身/固定数据。"
        )

    def _blocked_connect_ex(self, address):
        if _loopback(address):
            return orig_connect_ex(self, address)
        raise AssertionError(
            f"测试环境中禁止外部网络连接（BUG-01 隔离）：{address!r}；请使用测试替身/固定数据。"
        )

    patcher = pytest.MonkeyPatch()
    patcher.setattr(socket.socket, "connect", _blocked_connect)
    patcher.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    yield
    patcher.undo()


@pytest.fixture(scope="session", autouse=True)
def _business_db_sentinel():
    """BUG-01 哨兵：整个测试会话期间，业务库 data/simple_quant.db 内容必须不变。"""
    real_db = Path(__file__).resolve().parent.parent / "data" / "simple_quant.db"
    if not real_db.exists():
        yield
        return

    import hashlib

    def _digest(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    before = _digest(real_db)
    yield
    after = _digest(real_db)
    assert before == after, f"业务数据库哨兵校验失败：{real_db} 在测试运行中被修改"