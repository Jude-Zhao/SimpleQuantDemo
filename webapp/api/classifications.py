"""Classification and constraints API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webapp.models.constraint_config import ConstraintConfig
from webapp.models.database import get_db
from webapp.schemas.classification import (
    ClassificationRuleCreate,
    ClassificationRuleResponse,
    ClassificationRuleUpdate,
    ClassificationResult,
    ConstraintsSaveRequest,
    OptimizationConstraints,
)
from webapp.services.classification_service import (
    classify_universe,
    create_rule,
    delete_rule,
    get_rule,
    list_rules,
    update_rule,
)
from webapp.services.constraint_service import DEFAULT_CONSTRAINTS, load_constraints

router = APIRouter(prefix="/api/classifications", tags=["classifications"])
constraints_router = APIRouter(prefix="/api/constraints", tags=["constraints"])


@router.get("/rules", response_model=list[ClassificationRuleResponse])
def get_rules(active_only: bool = True, db: Session = Depends(get_db)):
    """List all classification rules."""
    return list_rules(db, active_only=active_only)


@router.get("", response_model=list[ClassificationResult])
def get_classification(db: Session = Depends(get_db)):
    """Return the current classification of the universe (read-only).

    ``classify_universe`` is a pure computation over rules and universe items
    (no database writes), so this is a safe read-only view for pages that only
    need to display the current assignment.
    """
    return classify_universe(db)


@router.post("/rules", response_model=ClassificationRuleResponse)
def create_rule_endpoint(rule: ClassificationRuleCreate, db: Session = Depends(get_db)):
    """Create a new classification rule."""
    try:
        return create_rule(db, rule)
    except ValueError as e:
        # F14: 非法 config 结构写库前拒绝
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/rules/{rule_id}", response_model=ClassificationRuleResponse)
def update_rule_endpoint(rule_id: int, update: ClassificationRuleUpdate, db: Session = Depends(get_db)):
    """Update a classification rule."""
    try:
        rule = update_rule(db, rule_id, update)
    except ValueError as e:
        # F14: 非法 config 结构写库前拒绝，旧规则保持原值
        raise HTTPException(status_code=400, detail=str(e))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule_endpoint(rule_id: int, db: Session = Depends(get_db)):
    """Delete a classification rule."""
    success = delete_rule(db, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"success": True, "id": rule_id}


@router.post("/apply", response_model=list[ClassificationResult])
def apply_classification(db: Session = Depends(get_db)):
    """Apply all active classification rules to the current universe."""
    return classify_universe(db)


# ── Constraints ────────────────────────────────────────────────────────
# 默认值与加载逻辑统一在 webapp.services.constraint_service（B7），
# GET 与 strategy_service.load_core_constraints 共用同一实现。


@constraints_router.get("", response_model=OptimizationConstraints)
def get_constraints(db: Session = Depends(get_db)):
    """Get current optimization constraints configuration."""
    return load_constraints(db)


@constraints_router.put("", response_model=OptimizationConstraints)
def update_constraints(req: ConstraintsSaveRequest, db: Session = Depends(get_db)):
    """Update optimization constraints configuration (persisted to DB)."""
    data = req.constraints.model_dump()
    row = db.query(ConstraintConfig).order_by(ConstraintConfig.id).first()
    if row is None:
        row = ConstraintConfig(config=data)
        db.add(row)
    else:
        row.config = data
    db.commit()
    db.refresh(row)
    return OptimizationConstraints(**row.config)
