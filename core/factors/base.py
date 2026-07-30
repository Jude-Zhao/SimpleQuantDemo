"""Factor builder abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class FactorBuilder(ABC):
    """Base class for factors that output a date-by-security score matrix.

    Subclasses should set these class-level metadata attributes
    (used by the web UI for rendering factor info and parameter forms):

    - ``registry_name``: unique key for the factor registry
    - ``display_name``: human-readable name shown in the UI
    - ``category``: factor category, e.g. "动量", "价值", "波动率"
    - ``description``: one-paragraph explanation of the factor logic
    - ``formula``: plain-text formula, e.g. "MOM(t) = close(t)/close(t-N) - 1"
    - ``direction``: "positive" if higher values predict higher returns,
      "negative" otherwise
    - ``params_schema``: dict describing each constructor parameter so the
      UI can render a form. Each entry has: type, default, min, max, step, label
    """

    registry_name: str = ""
    display_name: str = ""
    category: str = ""
    description: str = ""
    formula: str = ""
    direction: str = "positive"
    params_schema: dict = {}

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

