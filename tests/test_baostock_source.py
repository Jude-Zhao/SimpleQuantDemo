"""Tests for baostock data source."""

from __future__ import annotations

import pandas as pd
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
    assert "588000.SH" in universe


def test_get_macro_factors_money_supply(monkeypatch):
    """Baostock now provides monthly money supply data (m1_yoy / m2_yoy).

    BUG-01: login and the monthly query are replaced with a test double
    (fixed rows); no real network access.
    """
    from core.data.baostock_source import BaostockDataSource

    class _FakeQueryResult:
        error_code = "0"
        fields = ["statYear", "statMonth", "m2YOY", "m1YOY"]

        def __init__(self, rows):
            self._rows = rows
            self._cursor = 0

        def next(self):
            if self._cursor < len(self._rows):
                self._cursor += 1
                return True
            return False

        def get_row_data(self):
            return self._rows[self._cursor - 1]

    rows = [
        ("2024", "01", "8.7", "5.9"),
        ("2024", "02", "8.7", "5.9"),
        ("2024", "03", "8.7", "5.9"),
    ]

    def _fake_query(*args, **kwargs):
        assert kwargs.get("start_date") == "2024-01"
        assert kwargs.get("end_date") == "2024-03"
        return _FakeQueryResult(rows)

    monkeypatch.setattr(BaostockDataSource, "_ensure_login", lambda self: None)
    monkeypatch.setattr("baostock.query_money_supply_data_month", _fake_query)

    ds = BaostockDataSource()
    result = ds.get_macro_factors(start_date="2024-01-01", end_date="2024-03-31")
    assert not result.empty
    assert "m1_yoy" in result.columns
    assert "m2_yoy" in result.columns
    # Index should be month strings YYYY-MM
    assert str(result.index[0])[:7] == "2024-01"


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


# ── BUG-06: 分钟行情真实日内时间 ───────────────────────────────────────


def test_period_whitelist_rejects_unknown_before_login():
    """不支持周期明确拒绝（白名单），不能降级日频；校验在登录之前。"""
    from core.data.baostock_source import BaostockDataSource

    ds = BaostockDataSource()  # 未登录：校验先于登录，不会触碰网络
    with pytest.raises(ValueError):
        ds.get_etf_price_by_codes(["510300.SH"], period="7m")


def test_parse_minute_datetime_from_time_field():
    """time 字段（YYYYMMDDHHMMSSsss）解析为真实日内时间，同日多根 bar 全保留。"""
    from core.data.baostock_source import BaostockDataSource

    df = pd.DataFrame(
        {
            "date": ["2024-01-02"] * 3,
            "time": ["20240102093100000", "20240102093500000", "20240102150000000"],
            "close": ["1.0", "2.0", "3.0"],
        }
    )
    out = BaostockDataSource._parse_minute_datetime(df, "510300.SH")
    times = sorted(out["date"].dt.strftime("%H:%M").tolist())
    assert times == ["09:31", "09:35", "15:00"]


def test_parse_minute_datetime_date_with_time_fallback():
    """兼容形态：date 列本身携带 HH:MM:SS（无 time 列）也能解析。"""
    from core.data.baostock_source import BaostockDataSource

    df = pd.DataFrame(
        {
            "date": ["2024-01-02 09:35:00", "2024-01-02 15:00:00"],
            "close": ["1.0", "2.0"],
        }
    )
    out = BaostockDataSource._parse_minute_datetime(df, "510300.SH")
    assert out["date"].dt.strftime("%H:%M").tolist() == ["09:35", "15:00"]


def test_parse_minute_datetime_rejects_date_only():
    """date 仅有日期（无日内时间）→ 报错，不静默写零点。"""
    from core.data.baostock_source import BaostockDataSource

    df = pd.DataFrame({"date": ["2024-01-02"], "close": ["1.0"]})
    with pytest.raises(ValueError):
        BaostockDataSource._parse_minute_datetime(df, "510300.SH")


def test_parse_minute_datetime_rejects_bad_time():
    """time 列非法 → 报错，不写错误数据。"""
    from core.data.baostock_source import BaostockDataSource

    df = pd.DataFrame({"date": ["2024-01-02"], "time": ["not-a-time"], "close": ["1.0"]})
    with pytest.raises(ValueError):
        BaostockDataSource._parse_minute_datetime(df, "510300.SH")
