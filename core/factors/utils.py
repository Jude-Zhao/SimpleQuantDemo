"""Utilities shared by factor implementations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pandas as pd

from core.factors.exceptions import FactorValidationError


def pivot_price_field(
    price_data: pd.DataFrame,
    field: str = "close",
    universe: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Pivot long-form ETF price data into a date-by-security matrix."""
    required_columns = {"date", "sec", field}
    missing_columns = required_columns - set(price_data.columns)
    if missing_columns:
        raise FactorValidationError(f"Price data missing columns: {sorted(missing_columns)}")

    data = price_data[["date", "sec", field]].copy()
    data["date"] = pd.to_datetime(data["date"]).dt.normalize()
    data["sec"] = data["sec"].astype(str).str.strip().str.upper()

    duplicate_keys = data.duplicated(["date", "sec"])
    if duplicate_keys.any():
        examples = data.loc[duplicate_keys, ["date", "sec"]].head(5).to_dict("records")
        raise FactorValidationError(f"Price data contains duplicate date/sec rows: {examples}")

    matrix = data.pivot(index="date", columns="sec", values=field).sort_index()
    matrix.index.name = "date"

    if universe is not None:
        universe_list = [str(code).strip().upper() for code in universe]
        missing_codes = [code for code in universe_list if code not in matrix.columns]
        if missing_codes:
            raise FactorValidationError(f"Price data missing universe codes: {missing_codes}")
        matrix = matrix.reindex(columns=universe_list)

    return matrix


def validate_factor_matrix(
    factor: pd.DataFrame,
    universe: Sequence[str],
    name: str = "factor",
) -> None:
    """Validate one factor matrix."""
    if factor.empty:
        raise FactorValidationError(f"{name} is empty.")
    if not isinstance(factor.index, pd.DatetimeIndex):
        raise FactorValidationError(f"{name} index must be a DatetimeIndex.")
    if factor.index.has_duplicates:
        duplicates = factor.index[factor.index.duplicated()].unique().strftime("%Y-%m-%d").tolist()[:5]
        raise FactorValidationError(f"{name} index contains duplicate dates: {duplicates}")

    expected_columns = [str(code).strip().upper() for code in universe]
    actual_columns = [str(column).strip().upper() for column in factor.columns]
    if actual_columns != expected_columns:
        raise FactorValidationError(
            f"{name} columns must exactly match universe order. "
            f"expected={expected_columns[:5]}..., actual={actual_columns[:5]}..."
        )
    if factor.columns.duplicated().any():
        duplicates = factor.columns[factor.columns.duplicated()].tolist()
        raise FactorValidationError(f"{name} contains duplicate columns: {duplicates}")


def validate_factor_panel(
    factor_panel: Mapping[str, pd.DataFrame],
    universe: Sequence[str],
) -> None:
    """Validate a dict[str, DataFrame] factor panel."""
    if not factor_panel:
        raise FactorValidationError("Factor panel is empty.")
    for factor_name, factor in factor_panel.items():
        if not factor_name:
            raise FactorValidationError("Factor panel contains an empty factor name.")
        validate_factor_matrix(factor, universe, name=factor_name)

