"""Factor API endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.schemas.factor import FactorComputeRequest, FactorComputeResponse, FactorMeta
from webapp.services.data_service import get_etf_list, get_etf_price
from webapp.services.factor_service import compute_factor, list_factors

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("", response_model=list[FactorMeta])
def get_factors():
    """Get metadata for all available factors."""
    return list_factors()


@router.post("/compute", response_model=FactorComputeResponse)
def compute_factor_endpoint(
    req: FactorComputeRequest,
    db: Session = Depends(get_db),
):
    """Compute a single factor with IC analysis and group returns.

    Uses cached ETF price data (fetched from baostock on cache miss).
    """
    # Get ETF list as the universe
    etfs = get_etf_list(db)
    universe = [e["sec_code"] for e in etfs]

    if not universe:
        raise HTTPException(status_code=400, detail="No ETFs available")

    # Fetch price data
    price_data = get_etf_price(
        db=db,
        sec_codes=universe,
        period="daily",
    )

    if price_data.empty:
        raise HTTPException(
            status_code=503,
            detail="Unable to fetch price data from data source",
        )

    try:
        return compute_factor(
            factor_name=req.factor_name,
            params=req.params,
            price_data=price_data,
            macro_data=pd.DataFrame(),
            universe=universe,
            horizon=req.horizon,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
