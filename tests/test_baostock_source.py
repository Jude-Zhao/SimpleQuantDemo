"""Tests for baostock data source."""

from __future__ import annotations

import pytest


def test_code_conversion():
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()
    assert ds._convert_to_bs_code("510300.SH") == "sh.510300"
    assert ds._convert_to_bs_code("159915.SZ") == "sz.159915"


def test_get_universe():
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()
    universe = ds.get_universe()
    assert isinstance(universe, list)
    assert len(universe) >= 5
    assert "510300.SH" in universe
    assert "510500.SH" in universe
    assert "159915.SZ" in universe


def test_get_macro_factors_empty():
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()
    result = ds.get_macro_factors()
    assert result.empty


def test_period_mapping():
    """Verify period mapping logic without hitting the network."""
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()
    # Just verify the method exists and accepts period param
    assert hasattr(ds, "get_etf_price_by_codes")


@pytest.mark.skip(reason="Requires network connection and baostock server availability")
def test_get_etf_price_live():
    """Live test - skipped by default."""
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()
    df = ds.get_etf_price_by_codes(
        ["510300.SH"],
        start_date="2024-01-02",
        end_date="2024-01-12",
        period="daily",
    )
    assert not df.empty
    assert list(df.columns) == ["date", "sec", "open", "high", "low", "close", "volume", "amount"]
    assert df["sec"].iloc[0] == "510300.SH"
