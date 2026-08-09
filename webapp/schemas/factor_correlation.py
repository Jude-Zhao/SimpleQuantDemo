"""Factor correlation Pydantic schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FactorCorrelationRequest(BaseModel):
    """Request to compute factor correlation matrix.

    ``granularity`` selects the unit of analysis:
    - ``instance``: correlation between individual factor *instances*
      (each identified by ``factor_ids`` such as ``momentum(20)``).
    - ``class``: correlation between category *scores* (built from the
      equal-weighted factors of each non-empty category).
    """

    granularity: Literal["instance", "class"] = "instance"
    factor_ids: list[str] = Field(default_factory=list)
    factor_names: list[str] = Field(default_factory=list)


class FactorCorrelationResponse(BaseModel):
    """Factor correlation matrix result."""

    granularity: str
    labels: list[str]
    correlation_matrix: list[list[float]]