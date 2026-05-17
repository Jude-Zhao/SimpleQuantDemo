"""Factor synthesis abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class FactorSynthesizer(ABC):
    """Base class for factor synthesis implementations."""

    @abstractmethod
    def synthesize(
        self,
        factor_panel: dict[str, pd.DataFrame],
        icir_data: dict[str, pd.Series],
        half_life_periods: int = 20,
    ) -> pd.DataFrame:
        """Return a synthesized factor matrix with index=date and columns=sec."""

