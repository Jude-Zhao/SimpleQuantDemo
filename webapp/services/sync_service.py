"""Synchronization service.

Manages background sync tasks with progress tracking for both ETF market
data and macro data. Tasks are kept in memory and tracked by UUID.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

import pandas as pd
from sqlalchemy.orm import Session

from webapp.models.market_data import EtfDailyBar, EtfMinuteBar
from webapp.services.data_service import _get_primary_source, _get_secondary_source, get_etf_list


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncType(str, Enum):
    ETF_DAILY = "etf_daily"
    ETF_MINUTE = "etf_minute"
    MACRO_DAILY = "macro_daily"
    MACRO_MONTHLY = "macro_monthly"


@dataclass
class SyncTask:
    task_id: str
    task_type: SyncType
    status: SyncStatus = SyncStatus.PENDING
    total: int = 0
    current: int = 0
    message: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


_tasks: dict[str, SyncTask] = {}
_tasks_lock = threading.Lock()


def create_task(task_type: SyncType) -> SyncTask:
    """Create a new sync task and store it in the registry."""
    task = SyncTask(
        task_id=str(uuid.uuid4()),
        task_type=task_type,
        status=SyncStatus.PENDING,
        start_time=datetime.now(),
    )
    with _tasks_lock:
        _tasks[task.task_id] = task
    return task


def get_task(task_id: str) -> SyncTask | None:
    """Get a sync task by ID."""
    with _tasks_lock:
        return _tasks.get(task_id)


def _update_task(task: SyncTask, **kwargs) -> None:
    """Update task fields in-place (caller must hold lock if needed)."""
    for key, value in kwargs.items():
        setattr(task, key, value)


# ── ETF daily sync ────────────────────────────────────────────────────


def start_etf_sync(
    db: Session,
    sec_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    period: str = "daily",
) -> SyncTask:
    """Start an ETF data sync in a background thread.

    Args:
        db: database session (a new session will be opened for the background thread)
        sec_codes: list of ETF codes, or None for all available ETFs
        start_date: start date (default 2021-01-04)
        end_date: end date (default today)
        period: "daily" or minute period

    Returns:
        SyncTask with pending status
    """
    from webapp.config import get_config
    from webapp.models.database import SessionLocal

    if sec_codes is None:
        sec_codes = [item["sec_code"] for item in get_etf_list()]

    task_type = SyncType.ETF_DAILY if period in ("daily", "d") else SyncType.ETF_MINUTE
    task = create_task(task_type)
    task.total = len(sec_codes)
    task.message = f"准备同步 {len(sec_codes)} 只 ETF"

    default_start = get_config().sync.default_start_date
    effective_start = start_date or default_start
    effective_end = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")

    thread = threading.Thread(
        target=_run_etf_sync,
        args=(task.task_id, list(sec_codes), effective_start, effective_end, period),
        daemon=True,
    )
    thread.start()
    return task


def _run_etf_sync(
    task_id: str,
    sec_codes: list[str],
    start_date: str,
    end_date: str,
    period: str,
) -> None:
    """Background worker for ETF sync."""
    from webapp.models.database import SessionLocal

    db = SessionLocal()
    task = get_task(task_id)
    if task is None:
        return

    try:
        with _tasks_lock:
            task.status = SyncStatus.RUNNING
            task.message = "开始同步..."

        primary = _get_primary_source()
        secondary = _get_secondary_source()

        success_count = 0
        failed_codes: list[str] = []
        total_rows = 0

        for i, sec_code in enumerate(sec_codes, 1):
            try:
                with _tasks_lock:
                    task.message = f"正在同步 {sec_code} ({i}/{len(sec_codes)})"

                # 1. Delete old data in the date range
                _delete_etf_range(db, sec_code, start_date, end_date, period)

                # 2. Fetch fresh data
                df = _fetch_etf_safe(primary, secondary, [sec_code], start_date, end_date, period)

                if df.empty:
                    failed_codes.append(sec_code)
                    with _tasks_lock:
                        task.current = i
                    continue

                # 3. Write new data
                rows_written = _write_etf_data(db, df, period)
                total_rows += rows_written
                success_count += 1

            except Exception as e:
                failed_codes.append(f"{sec_code}: {e}")

            with _tasks_lock:
                task.current = i

        with _tasks_lock:
            task.status = SyncStatus.COMPLETED
            task.end_time = datetime.now()
            task.message = f"同步完成：成功 {success_count} 只，失败 {len(failed_codes)} 只，共 {total_rows} 条数据"
            task.result = {
                "success_count": success_count,
                "failed_count": len(failed_codes),
                "failed_codes": failed_codes,
                "total_rows": total_rows,
            }

    except Exception as e:
        with _tasks_lock:
            task.status = SyncStatus.FAILED
            task.end_time = datetime.now()
            task.error = str(e)
            task.message = f"同步失败：{e}"
    finally:
        db.close()


def _delete_etf_range(
    db: Session,
    sec_code: str,
    start_date: str,
    end_date: str,
    period: str,
) -> int:
    """Delete ETF bars in a date range. Returns count of deleted rows."""
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    if period in ("daily", "d"):
        deleted = db.query(EtfDailyBar).filter(
            EtfDailyBar.sec_code == sec_code,
            EtfDailyBar.trade_date >= start.date(),
            EtfDailyBar.trade_date <= end.date(),
        ).delete(synchronize_session=False)
    else:
        deleted = db.query(EtfMinuteBar).filter(
            EtfMinuteBar.sec_code == sec_code,
            EtfMinuteBar.period == period,
            EtfMinuteBar.trade_datetime >= start,
            EtfMinuteBar.trade_datetime <= end,
        ).delete(synchronize_session=False)

    db.commit()
    return deleted


def _fetch_etf_safe(primary, secondary, sec_codes, start_date, end_date, period) -> pd.DataFrame:
    """Fetch ETF data from primary source, falling back to secondary."""
    try:
        if hasattr(primary, "get_etf_price_by_codes"):
            df = primary.get_etf_price_by_codes(
                sec_codes=sec_codes,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )
        else:
            df = primary.get_etf_price(start_date=start_date, end_date=end_date)
            if not df.empty:
                df = df[df["sec"].isin(sec_codes)]
        if not df.empty:
            return df
    except Exception:
        pass

    if secondary is not None:
        try:
            if hasattr(secondary, "get_etf_price_by_codes"):
                df = secondary.get_etf_price_by_codes(
                    sec_codes=sec_codes,
                    start_date=start_date,
                    end_date=end_date,
                    period=period,
                )
            else:
                df = secondary.get_etf_price(start_date=start_date, end_date=end_date)
                if not df.empty:
                    df = df[df["sec"].isin(sec_codes)]
            return df
        except Exception:
            pass

    return pd.DataFrame()


def _write_etf_data(db: Session, df: pd.DataFrame, period: str) -> int:
    """Write ETF data to cache. Returns number of rows written."""
    if df.empty:
        return 0

    count = 0
    if period in ("daily", "d"):
        for _, row in df.iterrows():
            sec = row["sec"]
            date_val = pd.to_datetime(row["date"]).date()
            bar_id = f"{sec}_{date_val.isoformat()}"

            bar = EtfDailyBar(
                id=bar_id,
                sec_code=sec,
                trade_date=date_val,
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
                source=row.get("source", "baostock"),
            )
            db.add(bar)
            count += 1
    else:
        for _, row in df.iterrows():
            sec = row["sec"]
            dt_val = pd.to_datetime(row["date"])
            bar_id = f"{sec}_{dt_val.strftime('%Y%m%d%H%M')}_{period}"

            bar = EtfMinuteBar(
                id=bar_id,
                sec_code=sec,
                trade_datetime=dt_val,
                period=period,
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
                source=row.get("source", "baostock"),
            )
            db.add(bar)
            count += 1

    db.commit()
    return count
