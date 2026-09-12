"""Tests for the AkShare data source (offline, BUG-05)."""

from __future__ import annotations

import pandas as pd
import pytest

from core.data.akshare_source import AkShareDataSource


def test_no_unadjusted_fallback(monkeypatch):
    """BUG-05: hfq 为空时不得切换未复权来源，直接返回空（上层保留旧数据）。"""
    monkeypatch.setattr(
        AkShareDataSource,
        "_fetch_hfq_price",
        lambda self, ak, sec_code, start_date, end_date: pd.DataFrame(),
    )
    # fallback 已移除：_fetch_sina_price 不应再存在
    assert not hasattr(AkShareDataSource, "_fetch_sina_price")

    ds = AkShareDataSource()
    out = ds.get_etf_price_by_codes(
        ["510300.SH"], start_date="2024-01-02", end_date="2024-01-03"
    )
    assert out.empty


def _install_tencent_stub(monkeypatch, hfq_rows: list[list], raw_rows: list[list]) -> None:
    def fake_fetch(hs_code: str, start: str, end: str, fq: str, retries: int = 3):
        return list(hfq_rows) if fq == "hfq" else list(raw_rows)

    monkeypatch.setattr(AkShareDataSource, "_tencent_fetch_range", staticmethod(fake_fetch))


def test_hfq_adj_factor_computed(monkeypatch):
    """adj_factor = hfq_close / raw_close（10拆1 例：复权价 11、现价 10 → 1.1）。"""
    hfq_rows = [
        ["2024-01-02", "10.0", "11.0", "10.5", "9.9", "1000", "10000"],
        ["2024-01-03", "11.0", "12.1", "11.5", "10.9", "1000", "10000"],
    ]
    raw_rows = [
        ["2024-01-02", "9.0", "10.0", "9.5", "8.9", "1000", "10000"],
        ["2024-01-03", "9.0", "11.0", "9.5", "8.9", "1000", "10000"],
    ]
    _install_tencent_stub(monkeypatch, hfq_rows, raw_rows)

    ds = AkShareDataSource()
    df = ds._fetch_hfq_price(None, "510300.SH", "2024-01-02", "2024-01-03")

    assert not df.empty
    assert df["adj_factor"].tolist() == pytest.approx([1.1, pytest.approx(12.1 / 11.0)])
    assert set(df["source"]) == {"akshare"}


def test_hfq_raw_missing_adj_factor_is_nan(monkeypatch):
    """raw 序列缺失时 adj_factor 显式置 NaN（不默认造 1）。"""
    hfq_rows = [
        ["2024-01-02", "10.0", "11.0", "10.5", "9.9", "1000", "10000"],
    ]
    _install_tencent_stub(monkeypatch, hfq_rows, [])

    ds = AkShareDataSource()
    df = ds._fetch_hfq_price(None, "510300.SH", "2024-01-02", "2024-01-02")

    assert not df.empty
    assert pd.isna(df["adj_factor"].iloc[0])
    # close 仍是后复权价，不是现价
    assert df["close"].iloc[0] == pytest.approx(11.0)
