"""Shared data cleaning helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import pandas as pd


ETF_PRICE_COLUMNS = ["date", "sec", "open", "high", "low", "close", "volume", "amount"]
ETF_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "amount"]


def normalize_datetime(value: str | pd.Timestamp | None) -> pd.Timestamp | None:
    """Convert one date-like value into a normalized pandas Timestamp."""
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="raise")
    return pd.Timestamp(parsed).normalize()


def normalize_date_series(values: Iterable[object]) -> pd.Series:
    """Convert mixed date strings such as YYYY-MM-DD and YYYY/M/D to normalized dates."""
    raw_values = pd.Series(values)
    try:
        parsed = pd.to_datetime(raw_values, errors="coerce", format="mixed")
    except ValueError:
        parsed = pd.to_datetime(raw_values, errors="coerce")
    if parsed.isna().any():
        bad_values = raw_values[parsed.isna()].drop_duplicates().head(5).tolist()
        raise ValueError(f"Unable to parse date values: {bad_values}")
    return parsed.dt.normalize()


def coerce_numeric_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Coerce selected columns to numeric values in a copy of df."""
    result = df.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def filter_by_date_range(
    df: pd.DataFrame,
    start_date: str | pd.Timestamp | None,
    end_date: str | pd.Timestamp | None,
    date_column: str | None = None,
) -> pd.DataFrame:
    """Filter a DataFrame by date column or DateTimeIndex."""
    start = normalize_datetime(start_date)
    end = normalize_datetime(end_date)

    if date_column is not None:
        dates = pd.to_datetime(df[date_column]).dt.normalize()
    else:
        dates = pd.to_datetime(df.index).normalize()

    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= dates >= start
    if end is not None:
        mask &= dates <= end
    return df.loc[mask].copy()


def standardize_etf_price(raw: pd.DataFrame) -> pd.DataFrame:
    """Return ETF price data with normalized date/sec and numeric OHLCV columns."""
    result = raw.copy()
    result.columns = [str(column).strip() for column in result.columns]
    if "date" not in result.columns:
        raise ValueError("ETF price data must contain a date column.")
    if "sec" not in result.columns:
        raise ValueError("ETF price data must contain a sec column.")

    result["date"] = normalize_date_series(result["date"])
    result["sec"] = result["sec"].astype(str).str.strip().str.upper()
    result = coerce_numeric_columns(result, ETF_NUMERIC_COLUMNS)
    return result[ETF_PRICE_COLUMNS].sort_values(["date", "sec"]).reset_index(drop=True)


def standardize_macro_factors(raw: pd.DataFrame) -> pd.DataFrame:
    """Return macro data indexed by normalized date with numeric factor columns."""
    result = raw.copy()
    result.columns = [str(column).strip() for column in result.columns]

    date_column = "date" if "date" in result.columns else result.columns[0]
    result[date_column] = normalize_date_series(result[date_column])
    result = result.rename(columns={date_column: "date"})

    factor_columns = [column for column in result.columns if column != "date"]
    result = coerce_numeric_columns(result, factor_columns)
    result = result.sort_values("date").drop_duplicates("date", keep="last")
    result = result.set_index("date")
    result.index.name = "date"
    return result


def align_macro_to_trading_dates(
    macro_data: pd.DataFrame,
    trading_dates: Sequence[pd.Timestamp],
    ffill: bool = True,
) -> pd.DataFrame:
    """Align macro factors to ETF trading dates."""
    normalized_dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates))).normalize()
    result = macro_data.copy()
    result.index = pd.DatetimeIndex(pd.to_datetime(result.index)).normalize()
    result = result.sort_index().reindex(normalized_dates)
    if ffill:
        result = result.ffill()
    result.index.name = "date"
    return result
