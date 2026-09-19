"""Macro data service layer.

Provides:
- Field metadata for daily and monthly macro factors
- Database query helpers for the webapp
- Sync orchestration: fetch from data sources (AkShare),
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


def _macro_sync_resource(frequency: str, start_date: str | None, end_date: str | None) -> dict:
    """F05: 构造与实际写入口径一致的锁资源区间（注册前调用）。

    日频写入闭区间 [start, end] → 注册半开 [start, end+1天)；月频实际按
    整月写入（含结束月）→ 注册半开 [起始月月初, 结束月次月月初)。None
    边界代表无界，保持 None。先校验日期格式与 start<=end。
    """
    from webapp.services.sync_service import _parse_sync_date

    if frequency == "monthly":
        try:
            start_p = pd.Period(start_date, freq="M") if start_date else None
            end_p = pd.Period(end_date, freq="M") if end_date else None
        except (ValueError, TypeError) as e:
            raise ValueError(f"日期格式非法：{e}") from e
        if start_p is not None and end_p is not None and start_p > end_p:
            raise ValueError(f"start_date 晚于 end_date：{start_date} > {end_date}")
        return {
            "key": frequency,
            "period": "",
            "start": start_p.to_timestamp() if start_p is not None else None,
            "end": (end_p + 1).to_timestamp() if end_p is not None else None,
        }
    start_ts = _parse_sync_date(start_date, "start_date") if start_date else None
    end_ts = _parse_sync_date(end_date, "end_date") if end_date else None
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        raise ValueError(f"start_date 晚于 end_date：{start_date} > {end_date}")
    return {
        "key": frequency,
        "period": "",
        "start": start_ts,
        "end": end_ts + pd.Timedelta(days=1) if end_ts is not None else None,
    }


def start_macro_sync(
    db: Session,
    frequency: str = "daily",
    start_date: str | None = None,
    end_date: str | None = None,
) -> MacroSyncTask:
    """Start a macro data sync in a background thread."""
    import uuid

    from webapp.config import get_config
    from webapp.services.sync_service import (
        register_sync_activity,
        release_sync_activity,
    )

    task_id = str(uuid.uuid4())
    # F05: 注册前把写入范围换算为与锁一致的半开区间（月频按整月）
    resources = [_macro_sync_resource(frequency, start_date, end_date)]
    max_tasks = get_config().sync.max_concurrent_tasks
    # A10: 宏观资源为 frequency；锁内原子检查容量/冲突并登记
    register_sync_activity(task_id, "macro", resources, max_tasks)
    try:
        task = MacroSyncTask(
            task_id=task_id,
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
    except Exception:
        release_sync_activity(task_id)
        raise
    return task


def _run_macro_sync(
    task_id: str,
    frequency: str,
    start_date: str | None,
    end_date: str | None,
) -> None:
    """Background worker for macro sync."""
    from webapp.models.database import SessionLocal

    # 外层 try/finally 保证 SessionLocal()/get_macro_task 本身失败时也释放
    # 活动登记（A10），否则该频率资源将永久 409 直到进程重启。
    db = None
    try:
        db = SessionLocal()
        task = get_macro_task(task_id)
        if task is None:
            return

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
        task = get_macro_task(task_id)
        if task is not None:
            with _macro_tasks_lock:
                task.status = MacroSyncStatus.FAILED
                task.end_time = datetime.now()
                task.error = str(e)
                task.message = f"宏观数据同步失败：{e}"
    finally:
        # A10: 完成/失败都释放活动登记
        from webapp.services.sync_service import release_sync_activity

        release_sync_activity(task_id)
        if db is not None:
            db.close()


def _sync_daily(db: Session, task: MacroSyncTask, start_date: str | None, end_date: str | None) -> None:
    """Fetch daily macro data from AkShare and merge it into SQLite.

    BUG-03: all sources are clipped to the requested range, so out-of-range
    returns are never written; rows outside the range are kept; missing new
    fields never clear old valid values (non-null merge). The merge and its
    commit form a single transaction — any failure rolls the whole range back.
    """
    from core.data.akshare_source import AkShareDataSource

    source = AkShareDataSource()
    df = source.get_macro_factors(start_date=start_date, end_date=end_date)

    if df.empty:
        raise RuntimeError("AkShare 未返回日频宏观数据")

    # 统一裁剪到请求区间（超范围返回不写入）
    if start_date or end_date:
        idx = pd.to_datetime(df.index)
        keep = pd.Series(True, index=df.index)
        if start_date:
            keep = keep & pd.Series(idx >= pd.Timestamp(start_date), index=df.index)
        if end_date:
            keep = keep & pd.Series(idx <= pd.Timestamp(end_date), index=df.index)
        df = df[keep]

    if df.empty:
        task.result = {"rows": 0, "columns": []}
        return

    try:
        count = _merge_daily_rows(db, df, start_date, end_date)
        db.commit()
    except Exception:
        db.rollback()
        raise
    task.result = {"rows": count, "columns": list(df.columns)}


def _merge_daily_rows(
    db: Session,
    df: pd.DataFrame,
    start_date: str | None,
    end_date: str | None,
) -> int:
    """Upsert daily rows: non-null new values win; missing fields keep old
    values (never treat None as a clear instruction). Returns row count."""
    q = db.query(MacroDaily)
    if start_date:
        q = q.filter(MacroDaily.trade_date >= start_date)
    if end_date:
        q = q.filter(MacroDaily.trade_date <= end_date)
    existing = {r.trade_date: r for r in q.all()}

    count = 0
    for idx, row in df.iterrows():
        trade_date = pd.Timestamp(idx).strftime("%Y-%m-%d")
        record = existing.get(trade_date)
        if record is None:
            record = MacroDaily(trade_date=trade_date)
            db.add(record)
        for f in DAILY_FIELDS:
            if f.name in df.columns:
                val = row.get(f.name)
                if pd.notna(val):
                    setattr(record, f.name, float(val))
        count += 1
    return count


def _sync_monthly(db: Session, task: MacroSyncTask, start_date: str | None, end_date: str | None) -> None:
    """Fetch monthly macro data from AkShare and merge into SQLite.

    BUG-03: months outside the requested range (inclusive of the end month,
    cross-year boundaries included) are never written; missing fields keep
    old valid values; the merge commits as a single transaction.
    """
    from core.data.akshare_source import ensure_akshare_available

    # CPI / PPI / aggregate financing from AkShare
    ak = ensure_akshare_available()
    cpi_df = _fetch_ak_monthly_series(ak, ak.macro_china_cpi_yearly(), "中国CPI年率报告")
    ppi_df = _fetch_ak_monthly_series(ak, ak.macro_china_ppi_yearly(), "中国PPI年率报告")
    shrzgm_df = _fetch_ak_shrzgm(ak.macro_china_shrzgm())

    # Combine into one monthly table
    data: dict[str, dict[str, float]] = {}

    for month, val in cpi_df.items():
        data.setdefault(month, {})["cpi_yoy"] = float(val)
    for month, val in ppi_df.items():
        data.setdefault(month, {})["ppi_yoy"] = float(val)
    for month, val in shrzgm_df.items():
        data.setdefault(month, {})["aggregate_financing"] = float(val)

    if not data:
        raise RuntimeError("未能获取任何月频宏观数据")

    # 统一裁剪：只保留请求区间内的月份（含结束月），跨年边界按 Period 比较
    data = {
        m: v
        for m, v in data.items()
        if _in_requested_months(m, start_date, end_date)
    }
    if not data:
        task.result = {"rows": 0, "months": [], "total_months": 0}
        return

    try:
        count = _merge_monthly_rows(db, data, start_date, end_date)
        db.commit()
    except Exception:
        db.rollback()
        raise
    task.result = {"rows": count, "months": sorted(data.keys())[:5], "total_months": len(data)}


def _in_requested_months(month: str, start_date: str | None, end_date: str | None) -> bool:
    """Check whether ``month`` (YYYY-MM) falls inside the requested range.

    The end boundary is inclusive; cross-year ranges (e.g. 2025-12 ~
    2026-02 → three months) are handled by Period comparison.
    """
    p = pd.Period(month, freq="M")
    if start_date and p < pd.Timestamp(start_date).to_period("M"):
        return False
    if end_date and p > pd.Timestamp(end_date).to_period("M"):
        return False
    return True


def _merge_monthly_rows(
    db: Session,
    data: dict[str, dict[str, float]],
    start_date: str | None,
    end_date: str | None,
) -> int:
    """Upsert monthly rows: non-null new values win; missing fields keep old
    values. Returns the number of months written/updated."""
    start_month = str(pd.Timestamp(start_date).to_period("M")) if start_date else None
    end_month = str(pd.Timestamp(end_date).to_period("M")) if end_date else None

    q = db.query(MacroMonthly)
    if start_month:
        q = q.filter(MacroMonthly.trade_month >= start_month)
    if end_month:
        q = q.filter(MacroMonthly.trade_month <= end_month)
    existing = {r.trade_month: r for r in q.all()}

    count = 0
    for month in sorted(data.keys()):
        row = data[month]
        record = existing.get(month)
        if record is None:
            record = MacroMonthly(trade_month=month)
            db.add(record)
        if "cpi_yoy" in row:
            record.cpi_yoy = row["cpi_yoy"]
        if "ppi_yoy" in row:
            record.ppi_yoy = row["ppi_yoy"]
        if "aggregate_financing" in row:
            record.aggregate_financing = row["aggregate_financing"]
        count += 1
    return count


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
