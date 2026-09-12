"""Market data API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.services.sync_service import (
    SyncCapacityError,
    SyncConflictError,
    SyncStatus,
    SyncType,
    get_task,
    start_etf_sync,
)

router = APIRouter(prefix="/api/market", tags=["market"])


# ── Sync endpoints ────────────────────────────────────────────────────


class EtfSyncRequest(BaseModel):
    sec_codes: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    period: str = "daily"


class SyncTaskResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    total: int
    current: int
    message: str
    start_time: str | None = None
    end_time: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@router.post("/sync/etf", response_model=SyncTaskResponse)
def sync_etf(req: EtfSyncRequest, db: Session = Depends(get_db)):
    """Trigger a full ETF data sync (single-transaction range replace).

    Sync runs in the background. Poll /api/market/sync/{task_id} for progress.
    """
    try:
        task = start_etf_sync(
            db=db,
            sec_codes=req.sec_codes,
            start_date=req.start_date,
            end_date=req.end_date,
            period=req.period,
        )
    except SyncConflictError as e:
        # A10: 与进行中的同步资源重叠 → 409
        raise HTTPException(status_code=409, detail=str(e))
    except SyncCapacityError as e:
        # A10: 并发任务满容量 → 429
        raise HTTPException(status_code=429, detail=str(e))
    return _task_to_response(task)


@router.get("/sync/{task_id}", response_model=SyncTaskResponse)
def get_sync_status(task_id: str):
    """Get sync task progress and result."""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_to_response(task)


def _task_to_response(task) -> SyncTaskResponse:
    return SyncTaskResponse(
        task_id=task.task_id,
        task_type=task.task_type.value if hasattr(task.task_type, "value") else str(task.task_type),
        status=task.status.value if hasattr(task.status, "value") else str(task.status),
        total=task.total,
        current=task.current,
        message=task.message,
        start_time=task.start_time.isoformat() if task.start_time else None,
        end_time=task.end_time.isoformat() if task.end_time else None,
        result=task.result if task.result else None,
        error=task.error,
    )
