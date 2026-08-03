"""Strategy execution API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.schemas.strategy import (
    StrategyMeta,
    StrategyRunDetail,
    StrategyRunListItem,
    StrategyRunRequest,
    StrategyRunSummary,
)
from webapp.services.strategy_service import (
    get_run,
    get_strategy_meta,
    list_runs,
    list_strategies,
    run_strategy,
)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyMeta])
def get_strategies():
    """Get metadata for all supported strategies."""
    return list_strategies()


@router.post("/run", response_model=StrategyRunSummary)
def run_strategy_endpoint(req: StrategyRunRequest, db: Session = Depends(get_db)):
    """Run a strategy synchronously and return the summary."""
    return run_strategy(db, req)


@router.get("/runs", response_model=list[StrategyRunListItem])
def get_runs(limit: int = 20, db: Session = Depends(get_db)):
    """List recent strategy run records."""
    return list_runs(db, limit=limit)


@router.get("/runs/{run_id}", response_model=StrategyRunDetail)
def get_run_detail(run_id: int, db: Session = Depends(get_db)):
    """Get the full detail of a single run record."""
    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/export")
def export_run_csv(run_id: int, db: Session = Depends(get_db)):
    """Export a run's NAV curve as CSV."""
    import csv
    import io

    run = get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    summary = run.result_summary or {}
    equity_curve = summary.get("equity_curve", {})

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "nav"])
    for date, nav in equity_curve.items():
        writer.writerow([date, nav])

    return JSONResponse(
        content={"filename": f"strategy_run_{run_id}_nav.csv", "csv": buffer.getvalue()},
    )