"""Portfolio optimizer abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class PortfolioOptimizer(ABC):
    """Base class for portfolio optimizers."""

    @abstractmethod
    def optimize(
        self,
        factor_scores: pd.Series,
        top_n: int = 5,
        max_weight: float = 0.5,
        min_weight: float = 0.0,
    ) -> pd.Series:
        """Return target weights indexed by security code."""

