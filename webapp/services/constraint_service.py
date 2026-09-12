"""约束配置服务（B7）。

约束默认值与加载的唯一实现：GET /api/constraints 与
strategy_service.load_core_constraints 共用；无持久配置时统一沿用
默认 ``single_max_weight=0.15``。不在此另建第二套默认值。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from webapp.models.constraint_config import ConstraintConfig
from webapp.schemas.classification import OptimizationConstraints

# Defaults used when no persisted config exists yet.
DEFAULT_CONSTRAINTS = OptimizationConstraints(
    single_max_weight=0.15,
    category_constraints=[],
)


def load_constraints(db: Session) -> OptimizationConstraints:
    """Load persisted constraints; fall back to the shared defaults."""
    row = db.query(ConstraintConfig).order_by(ConstraintConfig.id).first()
    if row is None or not row.config:
        return DEFAULT_CONSTRAINTS.model_copy(deep=True)
    return OptimizationConstraints(**row.config)
