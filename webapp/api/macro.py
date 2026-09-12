"""Macro data API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.services.macro_service import (
    get_daily_macro,
    get_monthly_macro,
    get_macro_task,
    list_fields,
    start_macro_sync,
)

router = APIRouter(prefix="/api/macro", tags=["macro"])


class MacroSyncRequest(BaseModel):
    frequency: str = "daily"  # daily | monthly
    start_date: str | None = None
    end_date: str | None = None


class MacroSyncResponse(BaseModel):
    task_id: str
    frequency: str
    status: str
    total: int
    current: int
    message: str
    start_time: str | None = None
    end_time: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@router.get("/fields")
def macro_fields(frequency: str | None = None):
    """List available macro fields, optionally filtered by frequency."""
    return list_fields(frequency)


@router.get("/daily")
def daily_macro(
    start_date: str | None = None,
    end_date: str | None = None,
    fields: str | None = None,
    db: Session = Depends(get_db),
):
    """Query daily macro data."""
    field_list = fields.split(",") if fields else None
    df = get_daily_macro(db, start_date, end_date, field_list)
    return _frame_to_response(df)


@router.get("/monthly")
def monthly_macro(
    start_month: str | None = None,
    end_month: str | None = None,
    fields: str | None = None,
    db: Session = Depends(get_db),
):
    """Query monthly macro data."""
    field_list = fields.split(",") if fields else None
    df = get_monthly_macro(db, start_month, end_month, field_list)
    return _frame_to_response(df)


@router.post("/sync", response_model=MacroSyncResponse)
def sync_macro(req: MacroSyncRequest, db: Session = Depends(get_db)):
    """Trigger a macro data sync (range-scoped merge)."""
    from webapp.services.sync_service import SyncCapacityError, SyncConflictError

    try:
        task = start_macro_sync(
            db=db,
            frequency=req.frequency,
            start_date=req.start_date,
            end_date=req.end_date,
        )
    except SyncConflictError as e:
        # A10: 与进行中的宏观同步重叠 → 409
        raise HTTPException(status_code=409, detail=str(e))
    except SyncCapacityError as e:
        # A10: 并发任务满容量 → 429
        raise HTTPException(status_code=429, detail=str(e))
    return _task_to_response(task)


@router.get("/sync/{task_id}", response_model=MacroSyncResponse)
def macro_sync_status(task_id: str):
    """Get macro sync task progress and result."""
    task = get_macro_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_response(task)


def _task_to_response(task) -> MacroSyncResponse:
    return MacroSyncResponse(
        task_id=task.task_id,
        frequency=task.frequency,
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        total=task.total,
        current=task.current,
        message=task.message,
        start_time=task.start_time.isoformat() if task.start_time else None,
        end_time=task.end_time.isoformat() if task.end_time else None,
        result=task.result if task.result else None,
        error=task.error,
    )


def _frame_to_response(df) -> dict[str, Any]:
    """Convert a date/month-indexed DataFrame to API response."""
    if df.empty:
        return {"dates": [], "fields": {}}

    index_col = df.index.name or "date"
    dates = [str(i) for i in df.index]
    fields_out: dict[str, list[float | None]] = {}
    for col in df.columns:
        fields_out[col] = [None if pd_isna(v) else float(v) for v in df[col].tolist()]

    return {"dates": dates, "index_name": index_col, "fields": fields_out}


def pd_isna(v) -> bool:
    """NaN check without importing pandas at module level."""
    if v is None:
        return True
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False
