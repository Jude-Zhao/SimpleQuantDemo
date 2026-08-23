"""Shared core package for SimpleQuantDemo."""

from core.evaluate import (
    BacktestSummary,
    FactorEvaluation,
    evaluate_factor,
)

__all__ = [
    "BacktestSummary",
    "FactorEvaluation",
    "evaluate_factor",
]