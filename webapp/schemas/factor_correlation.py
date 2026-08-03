"""Factor correlation Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel


class FactorCorrelationRequest(BaseModel):
    """Request to compute factor correlation matrix."""

    factor_names: list[str]


class FactorCorrelationResponse(BaseModel):
    """Factor correlation matrix result."""

    factor_names: list[str]
    correlation_matrix: list[list[float]]