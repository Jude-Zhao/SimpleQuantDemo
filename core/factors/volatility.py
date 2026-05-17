"""Volatility factor implementation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.utils import pivot_price_field, validate_factor_matrix


class VolatilityFactor(FactorBuilder):
    """N-day annualized volatility factor based on close-to-close returns."""

    def __init__(self, window: int = 20, annualization: int = 252) -> None:
        if window <= 1:
            raise ValueError("window must be greater than 1.")
        if annualization <= 0:
            raise ValueError("annualization must be positive.")
        self.window = window
        self.annualization = annualization

    @property
    def name(self) -> str:
        return f"volatility_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        returns = close.pct_change(fill_method=None)
        factor = returns.rolling(window=self.window, min_periods=self.window).std() * np.sqrt(self.annualization)
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor

