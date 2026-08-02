"""Universe management API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.models.database import get_db
from webapp.schemas.universe import (
    UniverseBatchAddRequest,
    UniverseItemCreate,
    UniverseItemResponse,
)
from webapp.services.data_service import get_etf_list
from webapp.services.universe_service import (
    add_universe_item,
    batch_add_universe,
    get_universe_item,
    list_active_universe,
    remove_universe_item,
)

router = APIRouter(prefix="/api/universe", tags=["universe"])


@router.get("", response_model=list[UniverseItemResponse])
def get_universe(db: Session = Depends(get_db)):
    """Get all active ETFs in the universe."""
    return list_active_universe(db)


@router.post("", response_model=list[UniverseItemResponse])
def add_to_universe(req: UniverseBatchAddRequest, db: Session = Depends(get_db)):
    """Batch add ETFs to the universe."""
    if not req.items:
        raise HTTPException(status_code=400, detail="No items provided")
    items = batch_add_universe(db, req.items)
    return items


@router.delete("/{sec_code}")
def remove_from_universe(sec_code: str, db: Session = Depends(get_db)):
    """Remove an ETF from the universe (soft delete)."""
    success = remove_universe_item(db, sec_code)
    if not success:
        raise HTTPException(status_code=404, detail="ETF not found in universe")
    return {"success": True, "sec_code": sec_code}


@router.get("/available")
def get_available_etfs(db: Session = Depends(get_db)):
    """Get list of ETFs available to add to the universe."""
    all_etfs = get_etf_list(db)
    active_codes = {item.sec_code for item in list_active_universe(db)}

    result = []
    for etf in all_etfs:
        result.append({
            **etf,
            "in_universe": etf["sec_code"] in active_codes,
        })
    return result
