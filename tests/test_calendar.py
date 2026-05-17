from __future__ import annotations

import pandas as pd
import pytest

from core.calendar import generate_rebalance_dates, get_trading_dates


def test_get_trading_dates_from_price_data() -> None:
    price_data = pd.DataFrame(
        {
            "date": ["2026-01-05", "2026-01-05", "2026-01-06"],
            "sec": ["A.SH", "B.SH", "A.SH"],
        }
    )

    dates = get_trading_dates(price_data)

    assert dates.tolist() == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-06")]


def test_generate_weekly_rebalance_dates_first_trading_day() -> None:
    trading_dates = pd.DatetimeIndex(
        [
            "2026-01-05",
            "2026-01-06",
            "2026-01-09",
            "2026-01-12",
            "2026-01-13",
        ]
    )

    rebalance_dates = generate_rebalance_dates(trading_dates, rebalance_freq="weekly", rebalance_day=0)

    assert rebalance_dates.tolist() == [pd.Timestamp("2026-01-05"), pd.Timestamp("2026-01-12")]


def test_generate_weekly_rebalance_dates_second_trading_day() -> None:
    trading_dates = pd.DatetimeIndex(
        [
            "2026-01-05",
            "2026-01-06",
            "2026-01-12",
            "2026-01-13",
        ]
    )

    rebalance_dates = generate_rebalance_dates(trading_dates, rebalance_freq="weekly", rebalance_day=1)

    assert rebalance_dates.tolist() == [pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-13")]


def test_generate_monthly_rebalance_dates() -> None:
    trading_dates = pd.DatetimeIndex(
        [
            "2026-01-30",
            "2026-02-02",
            "2026-02-03",
            "2026-03-02",
        ]
    )

    rebalance_dates = generate_rebalance_dates(trading_dates, rebalance_freq="monthly", rebalance_day=0)

    assert rebalance_dates.tolist() == [
        pd.Timestamp("2026-01-30"),
        pd.Timestamp("2026-02-02"),
        pd.Timestamp("2026-03-02"),
    ]


def test_generate_rebalance_dates_skips_short_periods() -> None:
    trading_dates = pd.DatetimeIndex(["2026-01-05", "2026-01-06", "2026-01-12"])

    rebalance_dates = generate_rebalance_dates(trading_dates, rebalance_freq="weekly", rebalance_day=1)

    assert rebalance_dates.tolist() == [pd.Timestamp("2026-01-06")]


def test_generate_rebalance_dates_rejects_invalid_args() -> None:
    with pytest.raises(ValueError):
        generate_rebalance_dates(pd.DatetimeIndex(["2026-01-05"]), rebalance_freq="daily")
    with pytest.raises(ValueError):
        generate_rebalance_dates(pd.DatetimeIndex(["2026-01-05"]), rebalance_day=-1)

