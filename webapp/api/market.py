"""Market data API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.services.data_service import get_etf_list, get_etf_price
from webapp.services.sync_service import SyncStatus, SyncType, get_task, start_etf_sync

router = APIRouter(prefix="/api/market", tags=["market"])


class EtfInfo(BaseModel):
    sec_code: str
    sec_name: str
    category: str


class KlineData(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float


class KlineResponse(BaseModel):
    sec_code: str
    period: str
    data: list[KlineData]


@router.get("/etf/list", response_model=list[EtfInfo])
def list_etfs(db: Session = Depends(get_db)):
    """Get list of available ETFs."""
    return get_etf_list(db)


@router.get("/etf/{sec_code}/kline", response_model=KlineResponse)
def get_etf_kline(
    sec_code: str,
    period: str = Query("daily", pattern="^(daily|1m|5m|15m|30m|60m)$"),
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Get ETF K-line data (daily or minute).

    Data is fetched from the primary source and cached locally.
    """
    df = get_etf_price(
        db=db,
        sec_codes=[sec_code],
        start_date=start_date,
        end_date=end_date,
        period=period,
    )

    data = []
    for _, row in df.iterrows():
        data.append(KlineData(
            date=str(row["date"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            amount=float(row["amount"]),
        ))

    return KlineResponse(sec_code=sec_code, period=period, data=data)


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
    """Trigger a full ETF data sync (deletes old data and re-fetches).

    Sync runs in the background. Poll /api/market/sync/{task_id} for progress.
    """
    task = start_etf_sync(
        db=db,
        sec_codes=req.sec_codes,
        start_date=req.start_date,
        end_date=req.end_date,
        period=req.period,
    )
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
