"""Momentum factor implementation."""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.utils import pivot_price_field, validate_factor_matrix


class MomentumFactor(FactorBuilder):
    """N-day close-to-close return factor."""

    def __init__(self, window: int = 5) -> None:
        if window <= 0:
            raise ValueError("window must be positive.")
        self.window = window

    @property
    def name(self) -> str:
        return f"momentum_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        factor = close.pct_change(periods=self.window, fill_method=None)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor

