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
from webapp.services.data_service import (
    _get_primary_source,
    _get_secondary_source,
    _validated_adj_factor,
    get_etf_list,
)


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


class SyncConflictError(Exception):
    """请求的同步资源与进行中的任务重叠（API 返回 409）。"""


class SyncCapacityError(Exception):
    """并发同步任务数已达上限（API 返回 429）。"""


# 活动同步请求登记表（进程内锁保护；仅同进程保证互斥，不声称跨 worker 互斥）。
# entry: {"task_id": str, "kind": "etf"|"macro", "resources": [{"key","period","start","end"}]}
# 资源：ETF 为 (sec_code, period)；宏观为 frequency。区间半开，None 边界代表无穷。
_active_requests: list[dict] = []
_activity_lock = threading.Lock()


def _interval_overlaps(a_start, a_end, b_start, b_end) -> bool:
    """半开区间重叠判定：a_start < b_end and b_start < a_end。"""
    lo_a = a_start if a_start is not None else pd.Timestamp.min
    hi_a = a_end if a_end is not None else pd.Timestamp.max
    lo_b = b_start if b_start is not None else pd.Timestamp.min
    hi_b = b_end if b_end is not None else pd.Timestamp.max
    return lo_a < hi_b and lo_b < hi_a


def _find_conflict(kind: str, resources: list[dict]) -> dict | None:
    """在同一 kind 内查找与请求资源重叠的活动资源。"""
    for entry in _active_requests:
        if entry["kind"] != kind:
            continue
        for r in entry["resources"]:
            for n in resources:
                if (
                    r["key"] == n["key"]
                    and r["period"] == n["period"]
                    and _interval_overlaps(r["start"], r["end"], n["start"], n["end"])
                ):
                    return r
    return None


def register_sync_activity(task_id: str, kind: str, resources: list[dict], max_tasks: int) -> None:
    """锁内原子检查容量与冲突并登记；调用方在锁外启动线程。

    一个批量请求任一资源冲突则整体拒绝（不拆分静默执行）。
    """
    with _activity_lock:
        if len(_active_requests) >= max_tasks:
            raise SyncCapacityError(
                f"并发同步任务已达上限（{max_tasks}），请等待进行中的任务完成"
            )
        conflict = _find_conflict(kind, resources)
        if conflict is not None:
            raise SyncConflictError(
                f"同步资源冲突：{conflict['key']}"
                + (f"({conflict['period']})" if conflict["period"] else "")
                + " 正在同步重叠区间"
            )
        _active_requests.append({"task_id": task_id, "kind": kind, "resources": resources})


def release_sync_activity(task_id: str) -> None:
    """完成/失败都释放；任务历史对象可保留，不把历史 completed 当活动锁。"""
    with _activity_lock:
        _active_requests[:] = [e for e in _active_requests if e["task_id"] != task_id]


def create_task(task_type: SyncType, task_id: str | None = None) -> SyncTask:
    """Create a new sync task and store it in the registry."""
    task = SyncTask(
        task_id=task_id or str(uuid.uuid4()),
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
        sec_codes = [item["sec_code"] for item in get_etf_list(db)]

    # A10: 证券先去重
    sec_codes = list(dict.fromkeys(sec_codes))

    task_type = SyncType.ETF_DAILY if period in ("daily", "d") else SyncType.ETF_MINUTE

    default_start = get_config().sync.default_start_date
    effective_start = start_date or default_start
    effective_end = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")

    start_ts = pd.Timestamp(effective_start)
    end_ts = pd.Timestamp(effective_end)
    resources = [
        {"key": sec, "period": period, "start": start_ts, "end": end_ts}
        for sec in sec_codes
    ]
    max_tasks = get_config().sync.max_concurrent_tasks

    # A10: 锁内原子检查容量/冲突并登记；任一资源冲突则整体拒绝，不创建 task。
    task_id = str(uuid.uuid4())
    register_sync_activity(task_id, "etf", resources, max_tasks)
    try:
        task = create_task(task_type, task_id=task_id)
        task.total = len(sec_codes)
        task.message = f"准备同步 {len(sec_codes)} 只 ETF"

        thread = threading.Thread(
            target=_run_etf_sync,
            args=(task.task_id, list(sec_codes), effective_start, effective_end, period),
            daemon=True,
        )
        thread.start()
    except Exception:
        # start 失败撤销登记
        release_sync_activity(task_id)
        raise
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

    # 外层 try/finally 保证 SessionLocal()/get_task 本身失败时也释放活动
    # 登记（A10），否则该 (sec,period) 资源将永久 409 直到进程重启。
    db = None
    try:
        db = SessionLocal()
        task = get_task(task_id)
        if task is None:
            return

        with _tasks_lock:
            task.status = SyncStatus.RUNNING
            task.message = "开始同步..."

        from webapp.config import get_config
        _jump_threshold = get_config().datasource.jump_threshold

        primary = _get_primary_source()
        secondary = _get_secondary_source()

        success_count = 0
        failed_codes: list[str] = []
        rejected_codes: list[str] = []
        total_rows = 0
        all_warnings: list[dict] = []
        per_symbol: list[dict] = []

        def _record(
            sec: str,
            status: str,
            source_name: str,
            reason: str = "",
            df_written: pd.DataFrame | None = None,
            rows: int = 0,
        ) -> None:
            """A10: 逐证券实际结果；written 范围仅在 commit 成功后按实际 frame 计算。"""
            entry = {
                "sec_code": sec,
                "status": status,
                "requested_start": start_date,
                "requested_end": end_date,
                "written_start": "",
                "written_end": "",
                "rows": rows,
                "source": source_name,
                "reason": reason,
            }
            if df_written is not None and not df_written.empty:
                entry["written_start"] = str(pd.Timestamp(df_written["date"].min()).date())
                entry["written_end"] = str(pd.Timestamp(df_written["date"].max()).date())
            per_symbol.append(entry)

        for i, sec_code in enumerate(sec_codes, 1):
            try:
                with _tasks_lock:
                    task.message = f"正在同步 {sec_code} ({i}/{len(sec_codes)})"

                # 1. 先获取：失败/为空不进入删除步骤，原数据保留
                df, source_name = _fetch_etf_safe(
                    primary, secondary, [sec_code], start_date, end_date, period
                )

                if df.empty:
                    failed_codes.append(f"{sec_code}: 源返回为空，保留原数据")
                    _record(sec_code, "failed", source_name, "源返回为空，保留原数据")
                    with _tasks_lock:
                        task.current = i
                    continue

                # 2. 断崖校验：剔除疑似未复权的拆分/异常跳变；有剔除即中止该标的替换，
                #    不得用剩余行宣称完整替换成功
                df, jump_warnings = _filter_jump_anomalies(df, threshold=_jump_threshold)
                if jump_warnings:
                    all_warnings.extend(jump_warnings)
                    rejected_codes.append(
                        f"{sec_code}: 跳变校验剔除 {len(jump_warnings)} 行，保留原数据未替换"
                    )
                    _record(
                        sec_code,
                        "rejected",
                        source_name,
                        f"跳变校验剔除 {len(jump_warnings)} 行，保留原数据未替换",
                    )
                    with _tasks_lock:
                        task.current = i
                    continue

                # BUG-05: adj_factor 缺失或含非法值时显式告警（按 NULL 保存，
                # 不默认造 1）
                if "adj_factor" not in df.columns:
                    all_warnings.append(
                        {"sec": sec_code, "date": "", "chg": None, "reason": "adj_factor 列缺失，按 NULL 保存"}
                    )
                elif df["adj_factor"].isna().any():
                    all_warnings.append(
                        {"sec": sec_code, "date": "", "chg": None, "reason": "adj_factor 含缺失/非法值，按 NULL 保存"}
                    )

                # 3. 单事务替换：删除与写入同一事务一次 commit，任何异常整体回滚
                rows_written = _replace_etf_range(
                    db, df, sec_code, start_date, end_date, period
                )
                total_rows += rows_written
                success_count += 1
                _record(sec_code, "success", source_name, df_written=df, rows=rows_written)

            except Exception as e:
                db.rollback()
                failed_codes.append(f"{sec_code}: {e}")
                _record(sec_code, "failed", "", reason=str(e))

            with _tasks_lock:
                task.current = i

        with _tasks_lock:
            task.status = SyncStatus.COMPLETED
            task.end_time = datetime.now()
            task.message = (
                f"同步完成：成功 {success_count} 只，失败 {len(failed_codes)} 只，"
                f"校验中止 {len(rejected_codes)} 只，共 {total_rows} 条数据"
            )
            task.result = {
                "success_count": success_count,
                "failed_count": len(failed_codes),
                "failed_codes": failed_codes,
                "rejected_count": len(rejected_codes),
                "rejected_codes": rejected_codes,
                "total_rows": total_rows,
                "warnings": all_warnings,
                "warning_count": len(all_warnings),
                "results": per_symbol,
            }

    except Exception as e:
        task = get_task(task_id)
        if task is not None:
            with _tasks_lock:
                task.status = SyncStatus.FAILED
                task.end_time = datetime.now()
                task.error = str(e)
                task.message = f"同步失败：{e}"
    finally:
        # A10: 完成/失败都释放活动登记
        release_sync_activity(task_id)
        if db is not None:
            db.close()


def _delete_etf_range_rows(
    db: Session,
    sec_code: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    period: str,
) -> int:
    """Delete ETF bars in a date range WITHOUT committing (transaction-scoped)."""
    if period in ("daily", "d"):
        deleted = db.query(EtfDailyBar).filter(
            EtfDailyBar.sec_code == sec_code,
            EtfDailyBar.trade_date >= start.date(),
            EtfDailyBar.trade_date <= end.date(),
        ).delete(synchronize_session=False)
    else:
        # BUG-06: 半开区间【开始日零点, 结束日下一日零点)，结束日盘中 bar
        # 属于替换范围
        deleted = db.query(EtfMinuteBar).filter(
            EtfMinuteBar.sec_code == sec_code,
            EtfMinuteBar.period == period,
            EtfMinuteBar.trade_datetime >= start.normalize(),
            EtfMinuteBar.trade_datetime < end.normalize() + pd.Timedelta(days=1),
        ).delete(synchronize_session=False)
    return deleted


def _replace_etf_range(
    db: Session,
    df: pd.DataFrame,
    sec_code: str,
    start_date: str,
    end_date: str,
    period: str,
) -> int:
    """BUG-02: fetch/validate 通过后，在同一事务内删除旧区间并写入，一次 commit。

    任何一步失败都整体回滚，保证“一次区间替换全部成功或全部不发生”，
    且失败标的不会向 Session 泄漏 pending 对象。written 仅在 commit
    成功后按实际写入行数返回。
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    try:
        _delete_etf_range_rows(db, sec_code, start, end, period)
        written = _write_etf_data(db, df, period, commit=False)
        db.commit()
        return written
    except Exception:
        db.rollback()
        raise


def _fetch_etf_safe(
    primary, secondary, sec_codes, start_date, end_date, period
) -> tuple[pd.DataFrame, str]:
    """Fetch ETF data from primary source, falling back to secondary.

    Returns ``(df, source_name)``；source_name 为 "primary"/"secondary"/""。
    """
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
            return df, "primary"
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
            return df, "secondary"
        except Exception:
            pass

    return pd.DataFrame(), ""


def _filter_jump_anomalies(
    df: pd.DataFrame,
    threshold: float = 15.0,
) -> tuple[pd.DataFrame, list[dict]]:
    """Drop rows whose daily close change exceeds ``threshold`` percent.

    Only applies to unadjusted fallback data. Tencent hfq rows carry an
    ``adj_factor`` column and are already properly adjusted, so a >15% move
    there is a real market event (e.g. the 2024-09 A-share rally) and must be
    kept. Rows without ``adj_factor`` (Sina unadjusted / Baostock fallback)
    can hide a share split cliff (~-50%), so they are dropped with a warning
    to prevent bad rows from polluting factor calculations.
    """
    if df.empty:
        return df, []
    if "adj_factor" in df.columns:
        return df, []
    keep = pd.Series(True, index=df.index)
    warnings: list[dict] = []
    for sec in df["sec"].unique():
        sub = df[df["sec"] == sec].sort_values("date").copy()
        sub["chg"] = sub["close"].pct_change(fill_method=None) * 100
        bad = sub[sub["chg"].abs() > threshold]
        for _, r in bad.iterrows():
            warnings.append(
                {
                    "sec": sec,
                    "date": str(pd.Timestamp(r["date"]).date()),
                    "chg": round(float(r["chg"]), 2) if pd.notna(r["chg"]) else None,
                }
            )
        keep.loc[sub.index] = sub["chg"].fillna(0).abs() <= threshold
    return df[keep], warnings


def _write_etf_data(db: Session, df: pd.DataFrame, period: str, commit: bool = True) -> int:
    """Write ETF data to cache. Returns number of rows written.

    ``commit=False`` leaves the transaction open for the caller so delete
    and write can land in a single commit (BUG-02).
    """
    if df.empty:
        return 0

    count = 0
    if period in ("daily", "d"):
        for _, row in df.iterrows():
            sec = row["sec"]
            date_val = pd.to_datetime(row["date"]).date()
            bar_id = f"{sec}_{date_val.isoformat()}"

            adj_factor = _validated_adj_factor(row.get("adj_factor"))

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
                adj_factor=adj_factor,
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

    if commit:
        db.commit()
    return count
