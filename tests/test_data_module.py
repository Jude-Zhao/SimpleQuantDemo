from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.data import CsvDataSource
from core.data.exceptions import DataValidationError
from core.data.utils import normalize_date_series, standardize_macro_factors
from core.data.validators import validate_price_universe_coverage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_EXAMPLE = PROJECT_ROOT / "data_example"


def _example_paths() -> tuple[Path, Path, Path]:
    csv_paths = sorted(
        [path for path in DATA_EXAMPLE.iterdir() if path.suffix.lower() == ".csv"],
        key=lambda path: path.stat().st_size,
    )
    macro_path = csv_paths[0]
    etf_path = csv_paths[-1]
    universe_path = next(path for path in DATA_EXAMPLE.iterdir() if path.suffix.lower() == ".xlsx")
    return etf_path, macro_path, universe_path


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


class CsvDataSourceTests(unittest.TestCase):
    def test_load_all_example_data(self) -> None:
        etf_path, macro_path, universe_path = _example_paths()
        source = CsvDataSource(etf_path, macro_path, universe_path)

        price_data, macro_data, universe = source.load_all()

        self.assertEqual(price_data.shape, (43176, 8))
        self.assertEqual(macro_data.shape, (1542, 18))
        self.assertEqual(len(universe), 28)
        self.assertEqual(price_data["sec"].nunique(), 28)
        self.assertEqual(price_data["date"].min(), pd.Timestamp("2019-11-01"))
        self.assertEqual(price_data["date"].max(), pd.Timestamp("2026-03-13"))
        self.assertEqual(macro_data.index.min(), pd.Timestamp("2019-11-01"))
        self.assertEqual(macro_data.index.max(), pd.Timestamp("2026-03-13"))
        self.assertEqual(int(macro_data.isna().sum().sum()), 0)

    def test_load_all_supports_date_range(self) -> None:
        etf_path, macro_path, universe_path = _example_paths()
        source = CsvDataSource(etf_path, macro_path, universe_path)

        price_data, macro_data, universe = source.load_all(
            start_date="2026-03-02",
            end_date="2026-03-13",
        )

        self.assertEqual(len(universe), 28)
        self.assertEqual(price_data["date"].nunique(), 10)
        self.assertEqual(price_data.shape[0], 280)
        self.assertEqual(macro_data.shape[0], 10)
        self.assertEqual(price_data["date"].min(), pd.Timestamp("2026-03-02"))
        self.assertEqual(price_data["date"].max(), pd.Timestamp("2026-03-13"))

    def test_price_universe_mismatch_raises(self) -> None:
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

        with self.assertRaises(DataValidationError):
            validate_price_universe_coverage(price_data, ["159928.SZ"])

    def test_csv_source_reads_minimal_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            etf_path = tmp_path / "etf.csv"
            macro_path = tmp_path / "macro.csv"
            universe_path = tmp_path / "universe.xlsx"

            pd.DataFrame(
                {
                    "date": ["2026-01-02", "2026/1/5"],
                    "sec": ["510300.SH", "510300.SH"],
                    "open": [1.0, 1.1],
                    "high": [1.1, 1.2],
                    "low": [0.9, 1.0],
                    "close": [1.05, 1.15],
                    "volume": [100.0, 110.0],
                    "amount": [1000.0, 1200.0],
                }
            ).to_csv(etf_path, index=False)
            pd.DataFrame(
                {
                    "date": ["2026-01-02", "2026/1/5"],
                    "macro_a": [1.0, None],
                }
            ).to_csv(macro_path, index=False)
            pd.DataFrame({"证券代码": ["510300.SH"]}).to_excel(universe_path, index=False)

            source = CsvDataSource(etf_path, macro_path, universe_path)
            price_data, macro_data, universe = source.load_all()

            self.assertEqual(price_data["date"].tolist(), [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")])
            self.assertEqual(macro_data["macro_a"].tolist(), [1.0, 1.0])
            self.assertEqual(universe, ["510300.SH"])


if __name__ == "__main__":
    unittest.main()

