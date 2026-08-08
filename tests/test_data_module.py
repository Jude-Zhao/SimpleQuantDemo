from __future__ import annotations

import unittest

import pandas as pd
import pytest

from core.data.exceptions import DataValidationError
from core.data.utils import normalize_date_series, standardize_macro_factors
from core.data.validators import validate_price_universe_coverage


class DataUtilsTests(unittest.TestCase):
    def test_normalize_date_series_accepts_mixed_formats(self) -> None:
        dates = normalize_date_series(["2026-03-09", "2026/3/10", "2026/03/11"])
        self.assertEqual(
            [value.strftime("%Y-%m-%d") for value in dates],
            ["2026-03-09", "2026-03-10", "2026-03-11"],
        )

    def test_standardize_macro_factors_uses_first_column_as_date(self) -> None:
        raw = pd.DataFrame(
            {
                "Unnamed: 0": ["2026-03-09", "2026/3/10"],
                "factor_a": ["1.5", ""],
                "factor_b": ["2", "3"],
            }
        )

        result = standardize_macro_factors(raw)

        self.assertEqual(result.index.name, "date")
        self.assertEqual(result.shape, (2, 2))
        self.assertTrue(pd.api.types.is_numeric_dtype(result["factor_a"]))
        self.assertTrue(pd.isna(result.loc[pd.Timestamp("2026-03-10"), "factor_a"]))


def test_sqlite_source_load_all(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all()

    assert price_data.shape[1] == 8
    assert price_data["sec"].nunique() == len(universe)
    assert price_data["date"].min() == pd.Timestamp("2024-01-01")
    assert price_data["date"].max() == pd.Timestamp("2026-03-13")
    assert isinstance(macro_data.index, pd.DatetimeIndex)
    assert macro_data.index.name == "date"
    assert not macro_data.isna().all().all()
    assert len(universe) > 0


def test_sqlite_source_supports_date_range(sqlite_source) -> None:
    price_data, macro_data, universe = sqlite_source.load_all(
        start_date="2024-03-04",
        end_date="2024-03-08",
    )

    assert len(universe) > 0
    assert price_data["date"].nunique() == 5
    assert macro_data.shape[0] == 5
    assert price_data["date"].min() == pd.Timestamp("2024-03-04")
    assert price_data["date"].max() == pd.Timestamp("2024-03-08")


def test_price_universe_mismatch_raises() -> None:
    price_data = pd.DataFrame(
        {
            "date": [pd.Timestamp("2026-01-02")],
            "sec": ["510300.SH"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [100.0],
            "amount": [100.0],
        }
    )

    with pytest.raises(DataValidationError):
        validate_price_universe_coverage(price_data, ["159928.SZ"])


if __name__ == "__main__":
    unittest.main()