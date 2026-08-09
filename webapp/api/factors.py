"""Factor API endpoints."""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.schemas.factor import (
    FactorCategoryMeta,
    FactorComputeRequest,
    FactorComputeResponse,
)
from webapp.schemas.factor_correlation import (
    FactorCorrelationRequest,
    FactorCorrelationResponse,
)
from webapp.services.data_service import get_etf_list, get_etf_price
from webapp.services.factor_service import (
    compute_factor,
    compute_factor_correlation,
    list_factor_categories_meta,
    resolve_instance,
)

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("", response_model=list[FactorCategoryMeta])
def get_factors():
    """Get factors organized by the ``factors.yaml`` categories."""
    return list_factor_categories_meta()


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

    # Resolve the factor instance: prefer factor_id, else factor_name + params.
    if req.factor_id:
        name, params = resolve_instance(req.factor_id)
    elif req.factor_name:
        name, params = req.factor_name, req.params
    else:
        raise HTTPException(status_code=400, detail="factor_id or factor_name required")

    try:
        return compute_factor(
            factor_name=name,
            params=params,
            price_data=price_data,
            macro_data=pd.DataFrame(),
            universe=universe,
            horizon=req.horizon,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/correlation", response_model=FactorCorrelationResponse)
def factor_correlation_endpoint(
    req: FactorCorrelationRequest,
    db: Session = Depends(get_db),
):
    """Compute the cross-sectional correlation matrix between factors.

    ``granularity=instance`` correlates factor instances (by factor_ids);
    ``granularity=class`` correlates non-empty category scores.
    """
    etfs = get_etf_list(db)
    universe = [e["sec_code"] for e in etfs]

    if not universe:
        raise HTTPException(status_code=400, detail="No ETFs available")

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
        return compute_factor_correlation(
            req=req,
            price_data=price_data,
            macro_data=pd.DataFrame(),
            universe=universe,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
