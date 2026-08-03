"""Tests for the portfolio constraint validation module."""

from __future__ import annotations

from core.optimization.constraints import (
    CategoryConstraint,
    OptimizationConstraints,
    validate_constraints,
)


def _no_constraints() -> OptimizationConstraints:
    return OptimizationConstraints()


def test_no_violations_when_constraints_are_empty() -> None:
    weights = {"A.SH": 0.5, "B.SH": 0.5}
    classifications = {"A.SH": {"category": "宽基"}, "B.SH": {"category": "宽基"}}
    violations = validate_constraints(weights, classifications, _no_constraints())
    assert violations == []


def test_single_max_weight_violation() -> None:
    weights = {"A.SH": 0.6, "B.SH": 0.4}
    constraints = OptimizationConstraints(single_max_weight=0.5)
    violations = validate_constraints(weights, {}, constraints)
    assert len(violations) == 1
    assert "single_max_weight" in violations[0].constraint
    assert "A.SH" in violations[0].message


def test_single_min_weight_violation() -> None:
    weights = {"A.SH": 0.95, "B.SH": 0.05}
    constraints = OptimizationConstraints(single_min_weight=0.1)
    violations = validate_constraints(weights, {}, constraints)
    assert len(violations) == 1
    assert "single_min_weight" in violations[0].constraint
    assert "B.SH" in violations[0].message


def test_category_max_weight_violation() -> None:
    weights = {"A.SH": 0.6, "B.SH": 0.4}
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "商品"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_weight=0.4,
            )
        ]
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert len(violations) == 1
    assert "max_weight" in violations[0].constraint
    assert "宽基" in violations[0].message


def test_category_min_weight_violation() -> None:
    weights = {"A.SH": 0.6, "B.SH": 0.4}
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "商品"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="商品",
                min_weight=0.5,
            )
        ]
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert len(violations) == 1
    assert "min_weight" in violations[0].constraint
    assert "商品" in violations[0].message


def test_category_min_count_violation() -> None:
    weights = {"A.SH": 0.5, "B.SH": 0.5}
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                min_count=3,
            )
        ]
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert len(violations) == 1
    assert "min_count" in violations[0].constraint


def test_category_max_count_violation() -> None:
    weights = {"A.SH": 0.3, "B.SH": 0.3, "C.SH": 0.4}
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "宽基"},
        "C.SH": {"category": "商品"},
    }
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_count=1,
            )
        ]
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert len(violations) == 1
    assert "max_count" in violations[0].constraint


def test_missing_category_membership_does_not_violate() -> None:
    """Securities without a category assignment are not counted as members."""
    weights = {"A.SH": 1.0}
    classifications = {}
    constraints = OptimizationConstraints(
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_weight=0.5,
            )
        ]
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert violations == []


def test_multiple_violations_reported() -> None:
    weights = {"A.SH": 0.7, "B.SH": 0.3}
    classifications = {
        "A.SH": {"category": "宽基"},
        "B.SH": {"category": "商品"},
    }
    constraints = OptimizationConstraints(
        single_max_weight=0.5,
        category_constraints=[
            CategoryConstraint(
                category_key="category",
                category_value="宽基",
                max_weight=0.4,
            ),
        ],
    )
    violations = validate_constraints(weights, classifications, constraints)
    assert len(violations) == 2


def test_empty_portfolio_reports_violation() -> None:
    violations = validate_constraints({}, {}, _no_constraints())
    assert len(violations) == 1
    assert violations[0].constraint == "portfolio"