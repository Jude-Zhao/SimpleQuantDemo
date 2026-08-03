"""Portfolio constraint data structures and validation.

This module stays in the core layer and avoids Pydantic/SQLAlchemy
dependencies so it can be reused by both the research and webapp layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CategoryConstraint:
    """Per-category weight/count limits."""

    category_key: str
    category_value: str
    min_weight: Optional[float] = None
    max_weight: Optional[float] = None
    min_count: Optional[int] = None
    max_count: Optional[int] = None


@dataclass
class OptimizationConstraints:
    """Global portfolio constraints."""

    single_min_weight: Optional[float] = None
    single_max_weight: Optional[float] = None
    category_constraints: list[CategoryConstraint] = field(default_factory=list)
    turnover_limit: Optional[float] = None


@dataclass
class ConstraintViolation:
    """A single constraint violation."""

    constraint: str
    message: str
    severity: str = "error"  # "error" or "warning"


def validate_constraints(
    weights: dict[str, float],
    classifications: dict[str, dict[str, str]],
    constraints: OptimizationConstraints,
) -> list[ConstraintViolation]:
    """Validate a portfolio's weights against all constraints.

    Args:
        weights: ``{sec_code: weight}`` mapping.
        classifications: ``{sec_code: {category_key: category_value}}`` mapping.
        constraints: The constraints to validate against.

    Returns:
        A list of violations. An empty list means the portfolio is valid.
    """
    violations: list[ConstraintViolation] = []

    # Normalize: only securities with positive weight participate.
    positive = {code: w for code, w in weights.items() if w and w > 0}

    total_weight = sum(positive.values())
    if total_weight <= 0:
        violations.append(
            ConstraintViolation(
                constraint="portfolio",
                message="Portfolio has no positive weights.",
            )
        )
        return violations

    # ── Single-security constraints ────────────────────────────────────
    if constraints.single_min_weight is not None:
        for code, w in positive.items():
            if w < constraints.single_min_weight:
                violations.append(
                    ConstraintViolation(
                        constraint=f"single_min_weight[{code}]",
                        message=(
                            f"Weight {w:.4f} for {code} is below "
                            f"single_min_weight={constraints.single_min_weight:.4f}."
                        ),
                    )
                )

    if constraints.single_max_weight is not None:
        for code, w in positive.items():
            if w > constraints.single_max_weight:
                violations.append(
                    ConstraintViolation(
                        constraint=f"single_max_weight[{code}]",
                        message=(
                            f"Weight {w:.4f} for {code} exceeds "
                            f"single_max_weight={constraints.single_max_weight:.4f}."
                        ),
                    )
                )

    # ── Category constraints ───────────────────────────────────────────
    for cat in constraints.category_constraints:
        members = [
            code
            for code in positive
            if classifications.get(code, {}).get(cat.category_key) == cat.category_value
        ]
        cat_weight = sum(positive[code] for code in members)
        cat_count = len(members)

        if cat.min_weight is not None and cat_weight < cat.min_weight:
            violations.append(
                ConstraintViolation(
                    constraint=f"category[{cat.category_key}={cat.category_value}].min_weight",
                    message=(
                        f"Category '{cat.category_value}' weight {cat_weight:.4f} is "
                        f"below min_weight={cat.min_weight:.4f}."
                    ),
                )
            )
        if cat.max_weight is not None and cat_weight > cat.max_weight:
            violations.append(
                ConstraintViolation(
                    constraint=f"category[{cat.category_key}={cat.category_value}].max_weight",
                    message=(
                        f"Category '{cat.category_value}' weight {cat_weight:.4f} exceeds "
                        f"max_weight={cat.max_weight:.4f}."
                    ),
                )
            )
        if cat.min_count is not None and cat_count < cat.min_count:
            violations.append(
                ConstraintViolation(
                    constraint=f"category[{cat.category_key}={cat.category_value}].min_count",
                    message=(
                        f"Category '{cat.category_value}' count {cat_count} is below "
                        f"min_count={cat.min_count}."
                    ),
                )
            )
        if cat.max_count is not None and cat_count > cat.max_count:
            violations.append(
                ConstraintViolation(
                    constraint=f"category[{cat.category_key}={cat.category_value}].max_count",
                    message=(
                        f"Category '{cat.category_value}' count {cat_count} exceeds "
                        f"max_count={cat.max_count}."
                    ),
                )
            )

    # ── Turnover limit ─────────────────────────────────────────────────
    # turnover_limit requires a previous portfolio to compare against, so
    # it is validated by callers that have access to prior weights.

    return violations