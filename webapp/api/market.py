"""Market data API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.services.data_service import get_etf_list, get_etf_price

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
