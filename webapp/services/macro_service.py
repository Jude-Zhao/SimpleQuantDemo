"""Macro data service layer.

Provides:
- Field metadata for daily and monthly macro factors
- Database query helpers for the webapp
- Sync orchestration: fetch from data sources (AkShare / Baostock),
  compute derived fields, then write to SQLite (full overwrite).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from webapp.models.macro import MacroDaily, MacroMonthly

# ── Field metadata ──────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroFieldMeta:
    """Metadata for one macro field."""

    name: str
    label: str
    category: str
    unit: str
    frequency: str  # "daily" | "monthly"
    stability: str  # "high" | "medium"
    description: str = ""


DAILY_FIELDS: list[MacroFieldMeta] = [
    MacroFieldMeta("shibor_3m", "Shibor 3个月", "利率", "%", "daily", "high", "银行间资金成本"),
    MacroFieldMeta("fr007", "FR007 回购利率", "利率", "%", "daily", "medium", "质押式回购利率"),
    MacroFieldMeta("cn_gov_1y", "1年期国债收益率", "利率", "%", "daily", "medium", "短端无风险利率"),
    MacroFieldMeta("cn_gov_10y", "10年期国债收益率", "利率", "%", "daily", "medium", "长端无风险利率"),
    MacroFieldMeta("usd_cny", "美元/人民币中间价", "汇率", "元", "daily", "medium", "人民币汇率中间价"),
    MacroFieldMeta("copper", "沪铜主力收盘价", "商品", "元/吨", "daily", "high", "工业需求代理"),
    MacroFieldMeta("gold", "沪金主力收盘价", "商品", "元/克", "daily", "high", "避险与通胀代理"),
    MacroFieldMeta("rebar", "螺纹钢主力收盘价", "商品", "元/吨", "daily", "high", "建筑与周期需求"),
    MacroFieldMeta("csi300_pe", "沪深300滚动PE", "估值", "倍", "daily", "high", "大盘估值"),
    MacroFieldMeta("csi1000_pe", "中证1000滚动PE", "估值", "倍", "daily", "high", "小盘估值"),
    MacroFieldMeta("qvix_300etf", "300ETF期权波动率", "波动率", "指数点", "daily", "medium", "A股恐慌指数"),
    MacroFieldMeta("spx", "标普500指数", "海外", "指数点", "daily", "medium", "全球风险资产锚"),
    MacroFieldMeta("ixic", "纳斯达克指数", "海外", "指数点", "daily", "medium", "科技成长风格"),
    MacroFieldMeta("hsi", "恒生指数", "海外", "指数点", "daily", "medium", "港股风向标"),
]

MONTHLY_FIELDS: list[MacroFieldMeta] = [
    MacroFieldMeta("m2_yoy", "M2 同比", "货币", "%", "monthly", "high", "广义货币供应量"),
    MacroFieldMeta("m1_yoy", "M1 同比", "货币", "%", "monthly", "high", "狭义货币供应量"),
    MacroFieldMeta("m1_m2_scissors", "M1-M2 剪刀差", "货币", "百分点", "monthly", "high", "派生：m1_yoy - m2_yoy"),
    MacroFieldMeta("cpi_yoy", "CPI 同比", "经济", "%", "monthly", "high", "通胀水平"),
    MacroFieldMeta("ppi_yoy", "PPI 同比", "经济", "%", "monthly", "high", "工业品价格"),
    MacroFieldMeta("aggregate_financing", "社会融资规模增量", "经济", "亿元", "monthly", "high", "信用扩张"),
]

ALL_FIELDS: dict[str, MacroFieldMeta] = {f.name: f for f in DAILY_FIELDS + MONTHLY_FIELDS}


def list_fields(frequency: str | None = None) -> list[dict[str, str]]:
    """List macro field metadata, optionally filtered by frequency."""
    fields = DAILY_FIELDS if frequency == "daily" else MONTHLY_FIELDS if frequency == "monthly" else DAILY_FIELDS + MONTHLY_FIELDS
    return [f.__dict__ for f in fields]


# ── Query helpers ──────────────────────────────────────────────────


def get_daily_macro(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    fields: list[str] | None = None,
) -> pd.DataFrame:
    """Query daily macro data from SQLite.

    Returns DataFrame indexed by trade_date (str) with requested columns.
    """
    query = db.query(MacroDaily)
    if start_date:
        query = query.filter(MacroDaily.trade_date >= start_date)
    if end_date:
        query = query.filter(MacroDaily.trade_date <= end_date)

    rows = query.order_by(MacroDaily.trade_date).all()
    if not rows:
        return pd.DataFrame()

    data = [{**{"trade_date": r.trade_date}, **{f.name: getattr(r, f.name, None) for f in DAILY_FIELDS}} for r in rows]
    df = pd.DataFrame(data).set_index("trade_date")

    if fields:
        available = [f for f in fields if f in df.columns]
        return df[available]
    return df


def get_monthly_macro(
    db: Session,
    start_month: str | None = None,
    end_month: str | None = None,
    fields: list[str] | None = None,
) -> pd.DataFrame:
    """Query monthly macro data from SQLite.

    Returns DataFrame indexed by trade_month (str YYYY-MM) with requested columns.
    """
    query = db.query(MacroMonthly)
    if start_month:
        query = query.filter(MacroMonthly.trade_month >= start_month)
    if end_month:
        query = query.filter(MacroMonthly.trade_month <= end_month)

    rows = query.order_by(MacroMonthly.trade_month).all()
    if not rows:
        return pd.DataFrame()

    data = [{**{"trade_month": r.trade_month}, **{f.name: getattr(r, f.name, None) for f in MONTHLY_FIELDS}} for r in rows]
    df = pd.DataFrame(data).set_index("trade_month")

    if fields:
        available = [f for f in fields if f in df.columns]
        return df[available]
    return df


# ── Sync ──────────────────────────────────────────────────────────


class MacroSyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class MacroSyncTask:
    task_id: str
    frequency: str  # "daily" | "monthly"
    status: MacroSyncStatus = MacroSyncStatus.PENDING
    total: int = 0
    current: int = 0
    message: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


_macro_tasks: dict[str, MacroSyncTask] = {}
_macro_tasks_lock = threading.Lock()


def get_macro_task(task_id: str) -> MacroSyncTask | None:
    """Get a macro sync task by ID."""
    with _macro_tasks_lock:
        return _macro_tasks.get(task_id)


def start_macro_sync(
    db: Session,
    frequency: str = "daily",
    start_date: str | None = None,
    end_date: str | None = None,
) -> MacroSyncTask:
    """Start a macro data sync in a background thread."""
    from webapp.models.database import SessionLocal

    task = MacroSyncTask(
        task_id=str(uuid.uuid4()),
        frequency=frequency,
        status=MacroSyncStatus.PENDING,
        start_time=datetime.now(),
    )
    with _macro_tasks_lock:
        _macro_tasks[task.task_id] = task

    thread = threading.Thread(
        target=_run_macro_sync,
        args=(task.task_id, frequency, start_date, end_date),
        daemon=True,
    )
    thread.start()
    return task


def _run_macro_sync(
    task_id: str,
    frequency: str,
    start_date: str | None,
    end_date: str | None,
) -> None:
    """Background worker for macro sync."""
    from webapp.models.database import SessionLocal

    db = SessionLocal()
    task = get_macro_task(task_id)
    if task is None:
        return

    try:
        with _macro_tasks_lock:
            task.status = MacroSyncStatus.RUNNING
            task.message = "开始同步宏观数据..."

        if frequency == "daily":
            _sync_daily(db, task, start_date, end_date)
        elif frequency == "monthly":
            _sync_monthly(db, task, start_date, end_date)
        else:
            raise ValueError(f"Unknown frequency: {frequency}")

        with _macro_tasks_lock:
            task.status = MacroSyncStatus.COMPLETED
            task.end_time = datetime.now()
            task.message = f"宏观数据同步完成（{frequency}）"
    except Exception as e:
        with _macro_tasks_lock:
            task.status = MacroSyncStatus.FAILED
            task.end_time = datetime.now()
            task.error = str(e)
            task.message = f"宏观数据同步失败：{e}"
    finally:
        db.close()


def _sync_daily(db: Session, task: MacroSyncTask, start_date: str | None, end_date: str | None) -> None:
    """Fetch daily macro data from AkShare and overwrite SQLite."""
    from core.data.akshare_source import AkShareDataSource

    source = AkShareDataSource()
    df = source.get_macro_factors(start_date=start_date, end_date=end_date)

    if df.empty:
        raise RuntimeError("AkShare 未返回日频宏观数据")

    # Delete old rows in range
    deleted = db.query(MacroDaily).delete()
    db.commit()

    # Write new rows
    count = 0
    for idx, row in df.iterrows():
        trade_date = pd.Timestamp(idx).strftime("%Y-%m-%d")
        existing = db.query(MacroDaily).filter_by(trade_date=trade_date).first()
        if existing:
            continue

        record = MacroDaily(trade_date=trade_date)
        for f in DAILY_FIELDS:
            if f.name in df.columns:
                val = row.get(f.name)
                if pd.notna(val):
                    setattr(record, f.name, float(val))
        db.add(record)
        count += 1

    db.commit()
    task.result = {"rows": count, "columns": list(df.columns)}


def _sync_monthly(db: Session, task: MacroSyncTask, start_date: str | None, end_date: str | None) -> None:
    """Fetch monthly macro data from Baostock + AkShare and overwrite SQLite."""
    from core.data.baostock_source import BaostockDataSource
    from core.data.akshare_source import ensure_akshare_available

    # 1. Money supply from Baostock (m1_yoy, m2_yoy)
    bs_source = BaostockDataSource()
    money_df = bs_source.get_macro_factors(start_date=start_date, end_date=end_date)

    # 2. CPI / PPI / aggregate financing from AkShare
    ak = ensure_akshare_available()
    cpi_df = _fetch_ak_monthly_series(ak, ak.macro_china_cpi_yearly(), "中国CPI年率报告")
    ppi_df = _fetch_ak_monthly_series(ak, ak.macro_china_ppi_yearly(), "中国PPI年率报告")
    shrzgm_df = _fetch_ak_shrzgm(ak.macro_china_shrzgm())

    # Combine into one monthly table
    data: dict[str, dict[str, float]] = {}

    for month, val in money_df["m1_yoy"].dropna().items():
        data.setdefault(month, {})["m1_yoy"] = float(val)
    for month, val in money_df["m2_yoy"].dropna().items():
        data.setdefault(month, {})["m2_yoy"] = float(val)
    for month, val in cpi_df.items():
        data.setdefault(month, {})["cpi_yoy"] = float(val)
    for month, val in ppi_df.items():
        data.setdefault(month, {})["ppi_yoy"] = float(val)
    for month, val in shrzgm_df.items():
        data.setdefault(month, {})["aggregate_financing"] = float(val)

    if not data:
        raise RuntimeError("未能获取任何月频宏观数据")

    # Delete old rows
    db.query(MacroMonthly).delete()
    db.commit()

    count = 0
    for month in sorted(data.keys()):
        row = data[month]
        record = MacroMonthly(trade_month=month)
        if "m1_yoy" in row and "m2_yoy" in row:
            record.m1_yoy = row["m1_yoy"]
            record.m2_yoy = row["m2_yoy"]
            record.m1_m2_scissors = row["m1_yoy"] - row["m2_yoy"]  # derived
        elif "m1_yoy" in row:
            record.m1_yoy = row["m1_yoy"]
        elif "m2_yoy" in row:
            record.m2_yoy = row["m2_yoy"]
        if "cpi_yoy" in row:
            record.cpi_yoy = row["cpi_yoy"]
        if "ppi_yoy" in row:
            record.ppi_yoy = row["ppi_yoy"]
        if "aggregate_financing" in row:
            record.aggregate_financing = row["aggregate_financing"]
        db.add(record)
        count += 1

    db.commit()
    task.result = {"rows": count, "fields": sorted(data.keys())[:5], "total_months": len(data)}


def _fetch_ak_monthly_series(ak, df: pd.DataFrame, indicator_name: str) -> pd.Series:
    """Extract a monthly time series from AkShare yearly indicator tables."""
    if df.empty:
        return pd.Series(dtype=float)
    if "商品" not in df.columns or "日期" not in df.columns or "今值" not in df.columns:
        return pd.Series(dtype=float)
    # Filter to the indicator we want
    sub = df[df["商品"] == indicator_name] if indicator_name in df["商品"].values else df
    sub = sub.copy()
    sub["日期"] = pd.to_datetime(sub["日期"])
    sub["month"] = sub["日期"].dt.strftime("%Y-%m")
    sub["今值"] = pd.to_numeric(sub["今值"], errors="coerce")
    out = sub.drop_duplicates(subset="month", keep="last").set_index("month")["今值"].dropna()
    return out.sort_index()


def _fetch_ak_shrzgm(df: pd.DataFrame) -> pd.Series:
    """Extract aggregate financing series (月份 column is YYYYMM)."""
    if df.empty or "月份" not in df.columns or "社会融资规模增量" not in df.columns:
        return pd.Series(dtype=float)
    sub = df.copy()
    sub["月份"] = sub["月份"].astype(str)
    sub["month"] = sub["月份"].str.slice(0, 4) + "-" + sub["月份"].str.slice(4, 6)
    sub["value"] = pd.to_numeric(sub["社会融资规模增量"], errors="coerce")
    out = sub.set_index("month")["value"].dropna()
    return out.sort_index()
