"""Trading calendar helpers."""

from __future__ import annotations

from typing import Literal

import pandas as pd


RebalanceFrequency = Literal["weekly", "monthly"]


def get_trading_dates(price_data: pd.DataFrame) -> pd.DatetimeIndex:
    """Extract sorted unique trading dates from price data."""
    if "date" not in price_data.columns:
        raise ValueError("price_data must contain a date column.")
    dates = pd.DatetimeIndex(pd.to_datetime(price_data["date"]).dt.normalize().drop_duplicates())
    return dates.sort_values()


def generate_rebalance_dates(
    trading_dates: pd.DatetimeIndex | list[pd.Timestamp],
    rebalance_freq: RebalanceFrequency = "weekly",
    rebalance_day: int = 0,
) -> pd.DatetimeIndex:
    """Generate rebalance dates from trading dates.

    rebalance_day is the Nth trading day inside each period. The default 0
    selects the first trading day of each week/month.
    """
    if rebalance_day < 0:
        raise ValueError("rebalance_day must be non-negative.")
    if rebalance_freq not in {"weekly", "monthly"}:
        raise ValueError("rebalance_freq must be 'weekly' or 'monthly'.")

    dates = pd.DatetimeIndex(pd.to_datetime(list(trading_dates))).normalize().sort_values().unique()
    if dates.empty:
        return pd.DatetimeIndex([], name="date")

    grouped = pd.Series(dates, index=dates).groupby(_period_keys(dates, rebalance_freq))
    selected_dates = []
    for _, group in grouped:
        if len(group) > rebalance_day:
            selected_dates.append(group.iloc[rebalance_day])

    return pd.DatetimeIndex(selected_dates, name="date")


def _period_keys(dates: pd.DatetimeIndex, rebalance_freq: RebalanceFrequency) -> pd.Index:
    if rebalance_freq == "weekly":
        iso = dates.isocalendar()
        return pd.Index(iso["year"].astype(str) + "-" + iso["week"].astype(str).str.zfill(2))
    return pd.Index(dates.to_period("M").astype(str))

