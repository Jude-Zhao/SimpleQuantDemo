"""Dashboard summary API endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.models.strategy_run import StrategyRun
from webapp.services.data_service import get_etf_price, get_etf_list
from webapp.services.factor_service import list_factors
from webapp.services.universe_service import list_active_universe
from webapp.services.strategy_service import DEFAULT_END, DEFAULT_START

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class DashboardStats(BaseModel):
    universe_count: int
    factor_count: int
    run_count_today: int
    system_status: str


class PricePoint(BaseModel):
    date: str
    sec_code: str
    close: float


class FactorRankingItem(BaseModel):
    name: str
    display_name: str
    rank_ic_mean: float
    rank_icir: float


class ReturnRankingItem(BaseModel):
    sec_code: str
    sec_name: str
    return_pct: float


class ReturnRankingResponse(BaseModel):
    days: int
    as_of: str
    momentum: list[ReturnRankingItem]
    reversal: list[ReturnRankingItem]


class RecentRunItem(BaseModel):
    id: int
    strategy_type: str
    status: str
    total_return: float | None = None
    created_at: str | None = None
    error_msg: str | None = None


@router.get("/stats", response_model=DashboardStats)
def get_stats(db: Session = Depends(get_db)):
    """Get summary statistics for the dashboard cards."""
    from datetime import datetime

    universe_count = len(list_active_universe(db))
    factor_count = len(list_factors())

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    run_count_today = (
        db.query(StrategyRun)
        .filter(StrategyRun.created_at >= today_start)
        .count()
    )

    return DashboardStats(
        universe_count=universe_count,
        factor_count=factor_count,
        run_count_today=run_count_today,
        system_status="ok",
    )


@router.get("/etf-price", response_model=list[PricePoint])
def get_etf_price_series(
    codes: str = Query(..., description="Comma-separated ETF codes"),
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    """Get close-price series for multiple ETFs (long format)."""
    sec_codes = [c.strip() for c in codes.split(",") if c.strip()]
    if not sec_codes:
        return []

    df = get_etf_price(db, sec_codes, start or DEFAULT_START, end or DEFAULT_END)
    if df.empty:
        return []

    points = []
    for _, row in df.iterrows():
        points.append(PricePoint(
            date=str(pd.to_datetime(row["date"]).date()),
            sec_code=str(row["sec"]),
            close=float(row["close"]),
        ))
    return points


@router.get("/factor-ranking", response_model=list[FactorRankingItem])
def get_factor_ranking(db: Session = Depends(get_db)):
    """Compute RankIC mean/IR for recent factors for ranking display.

    Uses a lightweight computation from the first available date range.
    """
    etfs = get_etf_list(db)
    universe = [e["sec_code"] for e in etfs]
    if not universe:
        return []

    price_data = get_etf_price(db, universe, DEFAULT_START, DEFAULT_END)
    if price_data.empty:
        return []

    from webapp.services.factor_service import compute_factor

    ranking = []
    for meta in list_factors():
        try:
            result = compute_factor(
                factor_name=meta.name,
                params={},
                price_data=price_data,
                macro_data=pd.DataFrame(),
                universe=universe,
                horizon=5,
            )
            ranking.append(FactorRankingItem(
                name=result.factor_name,
                display_name=result.display_name,
                rank_ic_mean=result.ic_result.rank_ic_mean,
                rank_icir=result.ic_result.rank_icir,
            ))
        except Exception:
            # Skip factors that fail on the default data range.
            continue
    return ranking


@router.get("/returns-ranking", response_model=ReturnRankingResponse)
def get_returns_ranking(
    days: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Rank universe ETFs by trailing ``days``-day return.

    ``momentum`` is the top-10 highest-returning ETFs (buy-winners logic),
    ``reversal`` the bottom-10 lowest-returning ETFs (buy-losers logic).
    """
    etfs = get_etf_list(db)
    if not etfs:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])
    universe = [e["sec_code"] for e in etfs]
    name_map = {e["sec_code"]: e["sec_name"] for e in etfs}

    price_data = get_etf_price(db, universe, DEFAULT_START, DEFAULT_END)
    if price_data.empty:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    piv = price_data.pivot(index="date", columns="sec", values="close").sort_index()
    piv = piv.dropna(how="all")
    if len(piv) < 2:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    window = piv.iloc[-days:]
    if len(window) < 2:
        return ReturnRankingResponse(days=days, as_of="", momentum=[], reversal=[])

    ret = (window.iloc[-1] / window.iloc[0] - 1).dropna().sort_values(ascending=False)

    as_of_ts = piv.index[-1]
    as_of = str(as_of_ts.date()) if hasattr(as_of_ts, "date") else str(as_of_ts)

    def item(sec_code: str, return_pct: float) -> ReturnRankingItem:
        return ReturnRankingItem(
            sec_code=sec_code,
            sec_name=name_map.get(sec_code, sec_code),
            return_pct=float(return_pct),
        )

    momentum = [item(c, v) for c, v in ret.head(10).items()]
    reversal = [item(c, v) for c, v in ret.tail(10).sort_values(ascending=True).items()]

    return ReturnRankingResponse(days=days, as_of=as_of, momentum=momentum, reversal=reversal)


@router.get("/recent-runs", response_model=list[RecentRunItem])
def get_recent_runs(limit: int = 10, db: Session = Depends(get_db)):
    """Get recent strategy run records for the dashboard list."""
    runs = (
        db.query(StrategyRun)
        .order_by(StrategyRun.id.desc())
        .limit(limit)
        .all()
    )

    result = []
    for run in runs:
        total_return = None
        summary = run.result_summary or {}
        metrics = None
        # result_summary is stored as a raw dict; extract metrics if present.
        total_return = summary.get("metrics", {}).get("total_return") if isinstance(summary.get("metrics"), dict) else None

        result.append(RecentRunItem(
            id=run.id,
            strategy_type=run.strategy_type,
            status=run.status,
            total_return=total_return,
            created_at=run.created_at.isoformat() if run.created_at else None,
            error_msg=run.error_msg,
        ))
    return result