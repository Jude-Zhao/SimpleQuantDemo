"""Schema validation for data consumed by the quant pipeline."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pandas as pd

from core.data.exceptions import DataValidationError
from core.data.utils import ETF_PRICE_COLUMNS

SECURITY_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ)$")


def validate_etf_price(df: pd.DataFrame) -> None:
    """Validate ETF OHLCV data shape and basic keys."""
    missing_columns = [column for column in ETF_PRICE_COLUMNS if column not in df.columns]
    if missing_columns:
        raise DataValidationError(f"ETF price data missing columns: {missing_columns}")
    if df.empty:
        raise DataValidationError("ETF price data is empty.")
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        raise DataValidationError("ETF price date column must be datetime64.")
    if df["sec"].isna().any():
        raise DataValidationError("ETF price data contains empty sec values.")
    duplicate_keys = df.duplicated(["date", "sec"])
    if duplicate_keys.any():
        examples = df.loc[duplicate_keys, ["date", "sec"]].head(5).to_dict("records")
        raise DataValidationError(f"ETF price data contains duplicate date/sec rows: {examples}")


def validate_macro_factors(df: pd.DataFrame) -> None:
    """Validate daily macro factor table."""
    if df.empty:
        raise DataValidationError("Macro factor data is empty.")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataValidationError("Macro factor data index must be a DatetimeIndex.")
    if df.index.has_duplicates:
        duplicates = df.index[df.index.duplicated()].unique().strftime("%Y-%m-%d").tolist()[:5]
        raise DataValidationError(f"Macro factor data contains duplicate dates: {duplicates}")
    if df.columns.empty:
        raise DataValidationError("Macro factor data must contain at least one factor column.")
    if df.columns.duplicated().any():
        duplicates = df.columns[df.columns.duplicated()].tolist()
        raise DataValidationError(f"Macro factor data contains duplicate columns: {duplicates}")


def validate_universe(universe: Sequence[str]) -> None:
    """Validate the ETF security universe."""
    if not universe:
        raise DataValidationError("Universe is empty.")
    duplicates = sorted({code for code in universe if universe.count(code) > 1})
    if duplicates:
        raise DataValidationError(f"Universe contains duplicate codes: {duplicates}")
    invalid_codes = [code for code in universe if not SECURITY_CODE_PATTERN.match(code)]
    if invalid_codes:
        raise DataValidationError(f"Universe contains invalid security codes: {invalid_codes[:5]}")


def validate_price_universe_coverage(price_data: pd.DataFrame, universe: Sequence[str]) -> None:
    """Ensure every universe code has price data.

    The universe is a subset of the price table: the database may hold
    price history for more ETFs than the currently active universe, so
    extra codes in ``price_data`` are allowed. Only universe codes that
    are missing from price data are rejected.
    """
    price_codes = set(price_data["sec"].unique())
    universe_codes = set(universe)
    missing_in_price = sorted(universe_codes - price_codes)
    if missing_in_price:
        raise DataValidationError(
            f"Universe codes missing from price data: {missing_in_price}"
        )

