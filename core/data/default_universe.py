"""Single source of truth for the *static* default universe.

Three places used to each hard-code their own list and drifted apart:

- ``webapp.services.universe_service.DEFAULT_UNIVERSE`` (seed pool, only used
  when ``universe_items`` is empty to initialize a fresh DB)
- ``core.data.akshare_source.AkShareDataSource.get_universe()``
- ``core.data.baostock_source.BaostockDataSource.get_universe()``

All three now reference this one module so there is a single default list
instead of two/three. This is still a *fallback/seed* source only: the live
"current universe" always comes from ``universe_items`` (is_active=1). Keep
this list in sync with the active pool whenever the watchlist changes.

Current pool snapshot: the 30 active ETFs as of 2026-08-19 (adding 588000,
dropping the removed 159915).
"""

from __future__ import annotations

# (sec_code, sec_name, primary asset_type)
DEFAULT_UNIVERSE_ITEMS: list[tuple[str, str, str]] = [
    ("159908.SZ", "创业板ETF博时", "宽基"),
    ("159928.SZ", "消费ETF", "行业"),
    ("159929.SZ", "医药ETF", "行业"),
    ("159939.SZ", "信息技术ETF", "行业"),
    ("159941.SZ", "纳指ETF", "跨境"),
    ("159967.SZ", "创业板成长ETF", "宽基"),
    ("159985.SZ", "豆粕ETF", "商品"),
    ("510300.SH", "沪深300ETF", "宽基"),
    ("510500.SH", "中证500ETF", "宽基"),
    ("510880.SH", "红利ETF", "策略"),
    ("511010.SH", "国债ETF", "债券"),
    ("512000.SH", "券商ETF", "行业"),
    ("512040.SH", "价值100ETF", "策略"),
    ("512100.SH", "中证1000ETF", "宽基"),
    ("512200.SH", "房地产ETF", "行业"),
    ("512400.SH", "有色金属ETF", "行业"),
    ("512480.SH", "半导体ETF", "行业"),
    ("512580.SH", "环保ETF", "行业"),
    ("512680.SH", "军工ETF龙头", "行业"),
    ("512690.SH", "酒ETF", "行业"),
    ("512720.SH", "计算机ETF", "行业"),
    ("512800.SH", "银行ETF", "行业"),
    ("512980.SH", "传媒ETF", "行业"),
    ("513500.SH", "标普500ETF", "跨境"),
    ("513660.SH", "恒生ETF", "跨境"),
    ("513770.SH", "港股通互联网ETF", "跨境"),
    ("513880.SH", "日经225ETF", "跨境"),
    ("515880.SH", "通信ETF", "行业"),
    ("518880.SH", "黄金ETF", "商品"),
    ("588000.SH", "科创50ETF华夏", "宽基"),
]

DEFAULT_ACTIVE_CODES: list[str] = [code for code, _, _ in DEFAULT_UNIVERSE_ITEMS]