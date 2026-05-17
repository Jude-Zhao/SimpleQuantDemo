"""Recommended daily macro proxy field catalog.

The catalog is metadata only. Fetchers should read these specs and implement
provider-specific adapters without hard-coding field names in strategy code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Provider = Literal["akshare", "fred", "derived"]


@dataclass(frozen=True)
class MacroFieldSpec:
    """Metadata for one standardized daily macro field."""

    name: str
    provider: Provider
    meaning: str
    unit: str
    frequency: str = "daily"
    function: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    value_column: str | None = None
    date_column: str | None = None
    derived_from: tuple[str, ...] = ()
    expression: str | None = None
    note: str = ""


DEFAULT_DAILY_MACRO_CATALOG: dict[str, MacroFieldSpec] = {
    "shibor_3m": MacroFieldSpec(
        name="shibor_3m",
        provider="akshare",
        function="rate_interbank",
        params={"market": "上海银行同业拆借市场", "symbol": "Shibor人民币", "indicator": "3月"},
        date_column="报告日",
        value_column="利率",
        unit="%",
        meaning="Interbank funding cost and domestic liquidity condition.",
    ),
    "repo_fr007": MacroFieldSpec(
        name="repo_fr007",
        provider="akshare",
        function="repo_rate_hist",
        params={"symbol": "FR007"},
        value_column="利率",
        unit="%",
        meaning="Pledged repo fixing rate, a proxy for short-term RMB funding pressure.",
        note="Exact AkShare parameters should be confirmed against the installed package version.",
    ),
    "cn_gov_1y": MacroFieldSpec(
        name="cn_gov_1y",
        provider="akshare",
        function="bond_china_yield",
        params={"curve": "中债国债收益率曲线"},
        value_column="1年",
        unit="%",
        meaning="China 1Y government bond yield.",
    ),
    "cn_gov_10y": MacroFieldSpec(
        name="cn_gov_10y",
        provider="akshare",
        function="bond_china_yield",
        params={"curve": "中债国债收益率曲线"},
        value_column="10年",
        unit="%",
        meaning="China 10Y government bond yield.",
    ),
    "cn_term_spread_10y_1y": MacroFieldSpec(
        name="cn_term_spread_10y_1y",
        provider="derived",
        unit="pct_points",
        meaning="China term spread, reflecting growth and monetary policy expectations.",
        derived_from=("cn_gov_10y", "cn_gov_1y"),
        expression="cn_gov_10y - cn_gov_1y",
    ),
    "cn_credit_spread_aaa_5y": MacroFieldSpec(
        name="cn_credit_spread_aaa_5y",
        provider="derived",
        unit="pct_points",
        meaning="AAA credit spread, reflecting credit risk premium.",
        derived_from=("cn_aaa_note_5y", "cn_gov_5y"),
        expression="cn_aaa_note_5y - cn_gov_5y",
        note="Requires adding source fields cn_aaa_note_5y and cn_gov_5y from bond_china_yield.",
    ),
    "usd_index": MacroFieldSpec(
        name="usd_index",
        provider="akshare",
        function="index_global_hist_em",
        params={"symbol": "美元指数"},
        value_column="最新价",
        unit="index_points",
        meaning="US dollar strength and global liquidity/risk proxy.",
    ),
    "us_10y": MacroFieldSpec(
        name="us_10y",
        provider="fred",
        function="DGS10",
        value_column="DGS10",
        unit="%",
        meaning="US 10Y Treasury yield.",
        note="FRED is free but may be less stable from some domestic networks.",
    ),
    "us_real_10y": MacroFieldSpec(
        name="us_real_10y",
        provider="fred",
        function="DFII10",
        value_column="DFII10",
        unit="%",
        meaning="US 10Y TIPS real yield.",
        note="FRED is free but may be less stable from some domestic networks.",
    ),
    "brent_oil": MacroFieldSpec(
        name="brent_oil",
        provider="fred",
        function="DCOILBRENTEU",
        value_column="DCOILBRENTEU",
        unit="usd_per_barrel",
        meaning="Brent crude oil price, proxy for global inflation and energy demand.",
    ),
    "copper": MacroFieldSpec(
        name="copper",
        provider="akshare",
        function="futures_main_sina",
        params={"symbol": "CU0"},
        value_column="收盘价",
        unit="cny_per_ton",
        meaning="Copper futures main contract, proxy for industrial demand.",
    ),
    "gold": MacroFieldSpec(
        name="gold",
        provider="akshare",
        function="futures_main_sina",
        params={"symbol": "AU0"},
        value_column="收盘价",
        unit="cny_per_gram",
        meaning="Gold futures main contract, proxy for safe-haven demand and real-rate pressure.",
    ),
    "rebar": MacroFieldSpec(
        name="rebar",
        provider="akshare",
        function="futures_main_sina",
        params={"symbol": "RB0"},
        value_column="收盘价",
        unit="cny_per_ton",
        meaning="Rebar futures main contract, proxy for construction and cyclical demand.",
    ),
    "qvix_300etf": MacroFieldSpec(
        name="qvix_300etf",
        provider="akshare",
        function="index_option_300etf_qvix",
        value_column="close",
        unit="index_points",
        meaning="China equity option implied volatility proxy.",
    ),
    "csi300_pe": MacroFieldSpec(
        name="csi300_pe",
        provider="akshare",
        function="stock_zh_index_hist_csindex",
        params={"symbol": "000300"},
        value_column="滚动市盈率",
        unit="ratio",
        meaning="CSI 300 valuation proxy for large-cap equity liquidity/risk appetite.",
    ),
    "csi1000_pe": MacroFieldSpec(
        name="csi1000_pe",
        provider="akshare",
        function="stock_zh_index_hist_csindex",
        params={"symbol": "000852"},
        value_column="滚动市盈率",
        unit="ratio",
        meaning="CSI 1000 valuation proxy for small-cap equity liquidity/risk appetite.",
    ),
}


def get_macro_field_spec(name: str) -> MacroFieldSpec:
    """Return one macro field spec by standardized name."""
    return DEFAULT_DAILY_MACRO_CATALOG[name]


def list_macro_field_names(provider: Provider | None = None) -> list[str]:
    """List standardized macro field names, optionally filtered by provider."""
    if provider is None:
        return list(DEFAULT_DAILY_MACRO_CATALOG)
    return [
        name
        for name, spec in DEFAULT_DAILY_MACRO_CATALOG.items()
        if spec.provider == provider
    ]

