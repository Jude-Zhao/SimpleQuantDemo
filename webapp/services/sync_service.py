"""Synchronization service.

Manages background sync tasks with progress tracking for both ETF market
data and macro data. Tasks are kept in memory and tracked by UUID.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

import pandas as pd
from sqlalchemy.orm import Session

from webapp.models.market_data import EtfDailyBar
from webapp.services.data_service import (
    _get_primary_source,
    get_etf_list,
    upsert_coverage_rows,
    upsert_daily_bars,
)

logger = logging.getLogger(__name__)


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SyncType(str, Enum):
    ETF_DAILY = "etf_daily"
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
) -> SyncTask:
    """Start an ETF daily data sync in a background thread.

    Args:
        db: database session (a new session will be opened for the background thread)
        sec_codes: list of ETF codes, or None for all available ETFs
        start_date: start date (default 2021-01-04)
        end_date: end date (default today)

    Returns:
        SyncTask with pending status
    """
    from webapp.config import get_config
    from webapp.models.database import SessionLocal

    if sec_codes is None:
        sec_codes = [item["sec_code"] for item in get_etf_list(db)]

    # A10: 证券先去重
    sec_codes = list(dict.fromkeys(sec_codes))

    task_type = SyncType.ETF_DAILY

    default_start = get_config().sync.default_start_date
    effective_start = start_date or default_start
    effective_end = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")

    start_ts = pd.Timestamp(effective_start)
    end_ts = pd.Timestamp(effective_end)
    resources = [
        {"key": sec, "period": "daily", "start": start_ts, "end": end_ts}
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
            args=(task.task_id, list(sec_codes), effective_start, effective_end),
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
) -> None:
    """Background worker for ETF daily sync."""
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
        _ds_config = get_config().datasource
        _jump_threshold = _ds_config.jump_threshold
        _source_break_after = _ds_config.source_break_threshold

        primary = _get_primary_source()

        success_count = 0
        failed_codes: list[str] = []
        skipped_codes: list[str] = []
        consecutive_blocked = 0
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
                df, fetch_error = _fetch_etf_from(
                    primary, [sec_code], start_date, end_date
                )

                if df.empty:
                    reason = f"主源获取失败({fetch_error})，保留原数据"
                    failed_codes.append(f"{sec_code}: {reason}")
                    _record(sec_code, "failed", "primary", reason)
                    if fetch_error.startswith("TencentSourceError"):
                        # WAF 拦截为 IP 级封禁：连续 N 只源级失败即熔断，
                        # 剩余标的不再尝试，避免白烧请求加剧封锁
                        consecutive_blocked += 1
                        if consecutive_blocked >= _source_break_after:
                            skip_reason = "数据源连续拦截触发熔断，未尝试；建议稍后重试"
                            for rest in sec_codes[i:]:
                                skipped_codes.append(rest)
                                _record(rest, "skipped", "primary", skip_reason)
                            with _tasks_lock:
                                task.current = len(sec_codes)
                            break
                    else:
                        consecutive_blocked = 0
                    with _tasks_lock:
                        task.current = i
                    continue

                # 2. 断崖校验：仅告警不剔除——超阈值行照常写入，记入任务
                #    warnings 展示，保留可观测性且不误杀真实行情
                _, jump_warnings = _collect_jump_warnings(df, threshold=_jump_threshold)
                all_warnings.extend(jump_warnings)

                # 3. upsert 写入（F01）：不再整段删除——收到的行覆盖同键
                #    旧值，响应遗漏的日期保留旧行并告警；越界行丢弃并告警
                rows_written = _replace_etf_range(
                    db, df, sec_code, start_date, end_date
                )
                total_rows += rows_written
                success_count += 1
                consecutive_blocked = 0
                _record(sec_code, "success", "primary", df_written=df, rows=rows_written)

            except Exception as e:
                db.rollback()
                consecutive_blocked = 0
                failed_codes.append(f"{sec_code}: {e}")
                _record(sec_code, "failed", "primary", reason=str(e))

            with _tasks_lock:
                task.current = i

        with _tasks_lock:
            task.status = SyncStatus.COMPLETED
            task.end_time = datetime.now()
            message = (
                f"同步完成：成功 {success_count} 只，失败 {len(failed_codes)} 只，"
                f"共 {total_rows} 条数据"
            )
            if skipped_codes:
                message += f"，熔断跳过 {len(skipped_codes)} 只；数据源被限流，建议稍后重试同步"
            task.message = message
            task.result = {
                "success_count": success_count,
                "failed_count": len(failed_codes),
                "failed_codes": failed_codes,
                "skipped_count": len(skipped_codes),
                "skipped_codes": skipped_codes,
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


def _replace_etf_range(
    db: Session,
    df: pd.DataFrame,
    sec_code: str,
    start_date: str,
    end_date: str,
) -> int:
    """F01 最小修复：不再“整段删除 + 重写”，改为按 (sec_code, trade_date)
    原子 upsert 收到的行，响应遗漏的日期保留旧行并告警。

    原实现（BUG-02 单事务删除+重写）只能保证原子性：源返回非空但部分分
    段缺失/截断时，会把请求区间内全部旧数据替换成少量新行且照常提交，
    rollback 保护无效。upsert 语义下，“空结果不进入本函数、部分缺失只
    覆盖收到的日期、写入异常整体回滚”三种情况均保留旧有效行。轻校验：
    请求区间之外的行丢弃并告警，不写库。覆盖元数据与行情写入同一事务
    提交（保持 BUG-02 的单 commit 不变量）。
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    try:
        scoped = df
        dates = pd.to_datetime(scoped["date"])
        out_mask = (dates < start) | (dates > end)
        if out_mask.any():
            bad = sorted(
                {pd.Timestamp(d).date().isoformat() for d in dates[out_mask].unique()}
            )[:10]
            logger.warning(
                "同步收到请求区间之外的行情，已丢弃：sec=%s range=[%s, %s] dates=%s",
                sec_code, start_date, end_date, bad,
            )
            scoped = scoped[~out_mask]
        written = upsert_daily_bars(db, scoped, commit=False)
        _warn_omitted_dates(db, sec_code, start, end, scoped)
        upsert_coverage_rows(db, [sec_code], start_date, end_date, commit=False)
        db.commit()
        return written
    except Exception:
        db.rollback()
        raise


def _warn_omitted_dates(
    db: Session,
    sec_code: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    scoped: pd.DataFrame,
) -> None:
    """F01: 源响应遗漏请求区间内已有日期时告警（旧行保留，仅可观测）。

    不假定供应商截断一定发生，但把它从“静默丢数据”变成“显式告警”。
    """
    got = (
        {pd.Timestamp(d).date() for d in scoped["date"].unique()}
        if not scoped.empty
        else set()
    )
    db_dates = (
        db.query(EtfDailyBar.trade_date)
        .filter(
            EtfDailyBar.sec_code == sec_code,
            EtfDailyBar.trade_date >= start.date(),
            EtfDailyBar.trade_date <= end.date(),
        )
        .all()
    )
    omitted = sorted({d for (d,) in db_dates} - got)
    if omitted:
        shown = ", ".join(d.isoformat() for d in omitted[:10])
        more = f" 等 {len(omitted)} 天" if len(omitted) > 10 else ""
        logger.warning(
            "源响应遗漏请求区间内已有日期（旧行已保留）：sec=%s range=[%s, %s] dates=%s%s",
            sec_code, start.date(), end.date(), shown, more,
        )


def _fetch_etf_from(
    primary, sec_codes, start_date, end_date
) -> tuple[pd.DataFrame, str]:
    """Fetch ETF daily data from the primary source (single source, no fallback).

    Returns ``(df, error)``：df 为空时 error 给出具体失败原因（异常信息或
    "源返回为空"），供任务结果逐标的展示；成功时 error 为空字符串。
    """
    try:
        df = primary.get_etf_price_by_codes(
            sec_codes=sec_codes,
            start_date=start_date,
            end_date=end_date,
            period="daily",
        )
    except Exception as e:
        return pd.DataFrame(), f"{type(e).__name__}: {e}"
    if df.empty:
        return df, "源返回为空"
    return df, ""


def _collect_jump_warnings(
    df: pd.DataFrame,
    threshold: float = 15.0,
) -> tuple[pd.DataFrame, list[dict]]:
    """Collect rows whose daily close change exceeds ``threshold`` percent.

    仅告警不剔除：数据行始终原样返回（照常写入），超阈值行作为 warnings
    交由任务结果展示——保留对源数据毛刺的可观测性，同时不误杀真实行情
    （如 2024-09-30 大涨）。
    """
    warnings: list[dict] = []
    if df.empty:
        return df, warnings
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
    return df, warnings


def _write_etf_data(db: Session, df: pd.DataFrame, commit: bool = True) -> int:
    """Write ETF daily data to cache. Returns number of rows received.

    ``commit=False`` leaves the transaction open for the caller so the
    write can land in a single commit（F01：upsert，不删除遗漏日期的旧行）。
    """
    return upsert_daily_bars(db, df, commit=commit)
