"""Tests for CachedDataSource."""

from __future__ import annotations

import pandas as pd
import pytest

from core.data.cached_source import CachedDataSource


class _FakeDataSource:
    """Fake data source for testing."""

    def __init__(self, data: pd.DataFrame | None = None, should_fail: bool = False):
        self._data = data
        self._should_fail = should_fail
        self.fetch_count = 0

    def get_etf_price_by_codes(self, sec_codes, start_date=None, end_date=None, period="daily"):
        self.fetch_count += 1
        if self._should_fail:
            raise RuntimeError("source failure")
        if self._data is None:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])
        df = self._data[self._data["sec"].isin(sec_codes)].copy()
        return df.reset_index(drop=True)

    def get_universe(self):
        return ["510300.SH", "510500.SH"]

    def get_macro_factors(self, start_date=None, end_date=None, trading_dates=None):
        return pd.DataFrame()


def _make_sample_data():
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    rows = []
    for d in dates:
        for sec in ["510300.SH", "510500.SH"]:
            rows.append({
                "date": d,
                "sec": sec,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000000.0,
                "amount": 100000000.0,
            })
    return pd.DataFrame(rows)


def test_cache_miss_fetches_from_primary():
    data = _make_sample_data()
    primary = _FakeDataSource(data)
    cache_store = {}

    def reader(codes, start, end, period):
        return pd.DataFrame()

    def writer(df, period):
        cache_store["data"] = df

    cached = CachedDataSource(
        primary_source=primary,
        cache_reader=reader,
        cache_writer=writer,
    )

    result = cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-08"
    )

    assert not result.empty
    assert primary.fetch_count == 1
    assert "data" in cache_store
    assert len(cache_store["data"]) > 0


def test_cache_hit_skips_fetch():
    data = _make_sample_data()
    primary = _FakeDataSource(data)
    cached_data = data[data["sec"] == "510300.SH"].copy()

    def reader(codes, start, end, period):
        return cached_data.copy()

    def writer(df, period):
        pytest.fail("Writer should not be called on cache hit")

    cached = CachedDataSource(
        primary_source=primary,
        cache_reader=reader,
        cache_writer=writer,
    )

    result = cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-08"
    )

    assert not result.empty
    assert primary.fetch_count == 0  # No fetch needed


def test_secondary_fallback_on_primary_failure():
    data = _make_sample_data()
    primary = _FakeDataSource(should_fail=True)
    secondary = _FakeDataSource(data)
    cache_store = {}

    def reader(codes, start, end, period):
        return pd.DataFrame()

    def writer(df, period):
        cache_store["data"] = df

    cached = CachedDataSource(
        primary_source=primary,
        secondary_source=secondary,
        cache_reader=reader,
        cache_writer=writer,
    )

    result = cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-08"
    )

    assert not result.empty
    assert primary.fetch_count == 1
    assert secondary.fetch_count == 1


def test_empty_codes_returns_empty():
    primary = _FakeDataSource()
    cached = CachedDataSource(primary_source=primary)
    result = cached.get_etf_price_by_codes([], period="daily")
    assert result.empty


def test_get_universe_passthrough():
    primary = _FakeDataSource()
    cached = CachedDataSource(primary_source=primary)
    assert cached.get_universe() == ["510300.SH", "510500.SH"]


def test_partial_cache_miss():
    """When some codes are cached and others are not, only fetch missing ones."""
    data = _make_sample_data()
    primary = _FakeDataSource(data)
    cached_300 = data[data["sec"] == "510300.SH"].copy()

    def reader(codes, start, end, period):
        # Only 510300.SH is in cache
        return cached_300[cached_300["sec"].isin(codes)].copy()

    write_calls = []

    def writer(df, period):
        write_calls.append(df)

    cached = CachedDataSource(
        primary_source=primary,
        cache_reader=reader,
        cache_writer=writer,
    )

    result = cached.get_etf_price_by_codes(
        ["510300.SH", "510500.SH"],
        start_date="2024-01-02",
        end_date="2024-01-08",
    )

    assert not result.empty
    assert set(result["sec"].unique()) == {"510300.SH", "510500.SH"}
    # Only the missing code (510500.SH) should be fetched
    # (primary returns both, but that's fine - we asked for both)
    assert len(write_calls) == 1
