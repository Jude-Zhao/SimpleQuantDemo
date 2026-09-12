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


# ── BUG-04: 缓存不得将部分日期误判为完整命中 ───────────────────────────


def test_partial_date_hit_triggers_fetch():
    """直接复现：请求 01-05~01-06、缓存只有 01-05 → 触发源获取并返回两日。"""
    dates = pd.date_range("2024-01-04", periods=4, freq="B")  # 01-04..01-09
    rows = []
    for d in dates:
        rows.append({"date": d, "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1, "amount": 1})
    primary = _FakeDataSource(pd.DataFrame(rows))

    cache_date = pd.Timestamp("2024-01-05")
    cached_data = pd.DataFrame(
        [{"date": cache_date, "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 9.0, "volume": 1, "amount": 1}]
    )

    def reader(codes, start, end, period):
        return cached_data.copy()

    cached = CachedDataSource(primary_source=primary, cache_reader=reader)

    result = cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-05", end_date="2024-01-08"
    )

    assert primary.fetch_count == 1  # 部分日期必须触发获取
    result_dates = set(result["date"].dt.strftime("%Y-%m-%d"))
    assert {"2024-01-05", "2024-01-08"}.issubset(result_dates)  # 缺失日已补齐
    # 新数据覆盖缓存旧值
    row_05 = result[result["date"] == cache_date].iloc[0]
    assert row_05["close"] == 1.0
    assert not result.duplicated(subset=["date", "sec"]).any()


def test_full_range_covered_skips_fetch():
    """请求区间被缓存首尾覆盖 → 命中，不触发源调用。"""
    data = _make_sample_data()  # 2024-01-02..01-08，两个证券
    primary = _FakeDataSource(data)
    cached_data = data.copy()

    def reader(codes, start, end, period):
        df = cached_data.copy()
        if start is not None:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["date"] <= pd.Timestamp(end)]
        return df

    cached = CachedDataSource(primary_source=primary, cache_reader=reader)

    result = cached.get_etf_price_by_codes(
        ["510300.SH", "510500.SH"], start_date="2024-01-03", end_date="2024-01-05"
    )

    assert primary.fetch_count == 0
    assert len(result) == 6  # 2 证券 × 3 交易日（01-03..01-05）


def test_weekend_not_reported_as_gap():
    """跨周末/节假日请求不误报交易缺口：缓存首尾覆盖即命中（含周末自然日）。"""
    data = _make_sample_data()  # 5 个交易日 01-02(Tue)..01-08(Mon)，含周末 01-06/07
    primary = _FakeDataSource(data)
    cached_data = data.copy()

    def reader(codes, start, end, period):
        return cached_data.copy()

    cached = CachedDataSource(primary_source=primary, cache_reader=reader)

    # 请求范围包含周末自然日（01-06/07 是周六日），缓存无这些自然日的行
    cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-08"
    )
    assert primary.fetch_count == 0  # 不按自然日数量推断缺口


def test_primary_missing_secondary_receives_missing_code():
    """主源只有 A 时，备用源收到缺失的 B。"""
    dates = pd.date_range("2024-01-02", periods=3, freq="B")
    rows_a = [
        {"date": d, "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1, "amount": 1}
        for d in dates
    ]
    rows_b = [
        {"date": d, "sec": "510500.SH", "open": 2, "high": 2, "low": 2, "close": 2.0, "volume": 1, "amount": 1}
        for d in dates
    ]
    primary = _FakeDataSource(pd.DataFrame(rows_a))  # 主源只有 A
    secondary = _FakeDataSource(pd.DataFrame(rows_b))  # 备用源有 B

    def reader(codes, start, end, period):
        return pd.DataFrame()

    cached = CachedDataSource(
        primary_source=primary,
        secondary_source=secondary,
        cache_reader=reader,
    )

    result = cached.get_etf_price_by_codes(
        ["510300.SH", "510500.SH"], start_date="2024-01-02", end_date="2024-01-04"
    )

    assert primary.fetch_count == 1
    assert secondary.fetch_count == 1
    assert set(result["sec"].unique()) == {"510300.SH", "510500.SH"}


def test_merge_no_duplicate_keys():
    """合并无重复键 (sec, date)：新数据覆盖缓存旧值。"""
    dates = pd.date_range("2024-01-02", periods=2, freq="B")  # 01-02, 01-03
    cached_data = pd.DataFrame(
        [{"date": dates[0], "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 9.0, "volume": 1, "amount": 1}]
    )
    fresh = pd.DataFrame(
        [
            {"date": dates[0], "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 11.0, "volume": 1, "amount": 1},
            {"date": dates[1], "sec": "510300.SH", "open": 1, "high": 1, "low": 1, "close": 11.0, "volume": 1, "amount": 1},
        ]
    )
    primary = _FakeDataSource(fresh)

    def reader(codes, start, end, period):
        return cached_data.copy()

    cached = CachedDataSource(primary_source=primary, cache_reader=reader)

    result = cached.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-03"
    )

    assert not result.duplicated(subset=["date", "sec"]).any()
    assert len(result) == 2
    assert result[result["date"] == dates[0]]["close"].iloc[0] == 11.0


def test_unbounded_request_hits_cache_without_fetch():
    """无边界请求（语义为读取缓存全部历史）命中时不触发源调用。"""
    data = _make_sample_data()
    primary = _FakeDataSource(data)
    cached_data = data.copy()

    def reader(codes, start, end, period):
        return cached_data.copy()

    cached = CachedDataSource(primary_source=primary, cache_reader=reader)

    result = cached.get_etf_price_by_codes(["510300.SH", "510500.SH"])

    assert primary.fetch_count == 0
    assert len(result) == len(cached_data)
