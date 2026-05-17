"""Factor builder abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class FactorBuilder(ABC):
    """Base class for factors that output a date-by-security score matrix."""

    @abstractmethod
    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        """Build a factor matrix with index=date and columns=sec."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique factor name used in factor panels and logs."""

