"""Factor API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
import pandas as pd

from webapp.schemas.factor import FactorComputeRequest, FactorComputeResponse, FactorMeta
from webapp.services.factor_service import compute_factor, list_factors

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("", response_model=list[FactorMeta])
def get_factors():
    """Get metadata for all available factors."""
    return list_factors()


@router.post("/compute", response_model=FactorComputeResponse)
def compute_factor_endpoint(req: FactorComputeRequest):
    """Compute a single factor with IC analysis and group returns.

    NOTE: Currently uses synthetic test data. Will be connected to the
    real data source in a later task.
    """
    import numpy as np

    # Generate synthetic data for now (placeholder)
    np.random.seed(42)
    n_dates = 120
    n_sec = 15
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    secs = [f"ETF{i:02d}" for i in range(n_sec)]

    rows = []
    base_prices = np.linspace(50, 150, n_sec)
    for i, date in enumerate(dates):
        for j, sec in enumerate(secs):
            drift = 0.0003 * (j - n_sec / 2)
            noise = np.random.randn() * 0.012
            base_prices[j] *= (1 + drift + noise)
            rows.append({
                "date": date,
                "sec": sec,
                "close": round(base_prices[j], 4),
            })
    price_data = pd.DataFrame(rows)
    universe = secs

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
