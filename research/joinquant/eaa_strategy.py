# -*- coding: utf-8 -*-
"""
聚宽 ETF 多因子策略 —— EAA（Exponential Adaptive Allocation，幂乘合成，Top-N 分数加权）

对应 SimpleQuantDemo 项目的 EAA 策略（core/synthesis/faa_eaa.py + webapp 生产策略，
因子来自 core/factors/builtin/factors.yaml 的 5 个生产因子）。本文件为自包含单文件，
可直接粘贴到聚宽策略。

与 FAA 的区别：
- FAA：加权线性求和 L = Σ_k w_k·norm(cat_k)，入选组内等权。
- EAA：幂乘合成 S = ( Π_k norm(cat_k)^α_k )^β，仅 α_k>0 的分类参与，
       入选后按合成分分数成比例加权。

策略逻辑（已确认，来自 DESIGN.md + 生产 5 因子）：
1. 固定 30 只 ETF 标的池（横截面 min-max 归一化依赖固定池）。
2. 5 个因子按 4 个分类（momentum/reversal/volatility/volume）分组。
3. 每个因子按日做横截面 min-max 归一化到 (eps, 1]（eps=0.01）。
4. 分类内因子等权平均 -> 分类得分；分类得分再做一次横截面归一化。
5. EAA 合成：S = ( Π_k norm(cat_k)^α_k )^β。
6. 取合成分最高的 top_n=5 只，权重 ∝ 合成分（归一化到和为 1）。
7. 每 5 个交易日调仓一次；因子用前一交易日数据，当日开盘价成交（无前视）。

基准：中证 500（000905.XSHG）。标的为 ETF，用 type='fund' 佣金。
"""

# 聚宽函数库
from jqdata import *
import datetime
import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# 策略配置
# ---------------------------------------------------------------
# 标的池（数据库 is_active=1 的 30 只 ETF，本地 .SH/.SZ -> 聚宽 .XSHG/.XSHE）
UNIVERSE = [
    "159915.XSHE", "159928.XSHE", "159929.XSHE", "159939.XSHE", "159941.XSHE",
    "159967.XSHE", "159985.XSHE",
    "510300.XSHG", "510500.XSHG", "510880.XSHG", "511010.XSHG", "512000.XSHG",
    "512040.XSHG", "512100.XSHG", "512200.XSHG", "512400.XSHG", "512480.XSHG",
    "512580.XSHG", "512680.XSHG", "512690.XSHG", "512720.XSHG", "512800.XSHG",
    "512980.XSHG", "513500.XSHG", "513660.XSHG", "513770.XSHG", "513880.XSHG",
    "515880.XSHG", "518880.XSHG", "588000.XSHG",
]

TOP_N = 5           # 持仓数量
EPS = 0.01          # min-max 归一化下限系数
REBALANCE_PERIOD = 5  # 每 5 个交易日调仓
LOOKBACK_DAYS = 250   # 因子价格回看（自然日），覆盖最长 120 交易日窗口

# EAA 分类指数 α（默认：动量0.5/反转1.0/波动1.0/量能1.25）与整体指数 β
EXPONENTS = {
    "momentum": 0.5,
    "reversal": 1.0,
    "volatility": 1.0,
    "volume": 1.25,
}
BETA = 0.5

# 因子分类（与 core/factors/builtin/factors.yaml 生产 5 因子一致）
CATEGORIES = {
    "momentum": ["macd_hist"],
    "reversal": ["skewness_60_reversal"],
    "volatility": ["drawdown_120"],
    "volume": ["mfi", "psy20"],
}


def initialize(context):
    set_benchmark('000905.XSHG')
    set_option('use_real_price', True)
    set_order_cost(
        OrderCost(open_tax=0, close_tax=0,
                  open_commission=0.00025, close_commission=0.00025,
                  min_commission=0),
        type='fund',  # ETF
    )
    set_slippage(FixedSlippage(0))  # 与本地无滑点设定对齐
    log.set_level('order', 'error')

    # 调仓频率计数器：每 5 个交易日调仓一次
    g.rebalance_counter = 0
    # 每个交易日开盘运行，以当日开盘价成交
    run_daily(market_open, time='open')


# ---------------------------------------------------------------
# 取数：把 30 只 ETF 的横截面价量数据对齐成 DataFrame
# ---------------------------------------------------------------
def _load_panel(context, universe=UNIVERSE):
    """一次调仓日拉取所有标的，返回 pivot 后的 {field: DataFrame}。

    DataFrame index=交易日，columns=标的；未上市/停牌日为 NaN。
    截止到前一交易日（context.previous_date），保证用已收盘数据、无前视。
    使用后复权价（fq='post'）对齐本地 hfq。
    """
    end_date = context.previous_date  # 前一交易日（已收盘）
    start_date = end_date - datetime.timedelta(days=LOOKBACK_DAYS)
    fields = ['open', 'high', 'low', 'close', 'volume']

    per_sec = {}
    for sec in universe:
        try:
            df = get_price(sec, start_date=start_date, end_date=end_date,
                           frequency='daily', fields=fields, fq='post',
                           skip_paused=True)
            per_sec[sec] = df
        except Exception:
            per_sec[sec] = None

    panel = {}
    for f in fields:
        cols = {sec: (df[f] if df is not None else None)
                for sec, df in per_sec.items()}
        valid = {sec: s for sec, s in cols.items() if s is not None and not s.empty}
        if valid:
            panel[f] = pd.DataFrame(valid).sort_index()
        else:
            panel[f] = pd.DataFrame()
    return panel


# ---------------------------------------------------------------
# 因子计算（每个因子返回 DataFrame，index=交易日，columns=标的）
# 方向均为 positive：值越大得分越高
# ---------------------------------------------------------------
def factor_macd_hist(close):
    """(DIF-DEA)/close；DIF=EMA12-EMA26，DEA=EMA9(DIF)。"""
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return (dif - dea) / close.replace(0.0, np.nan)


def factor_skewness_60_reversal(close):
    """-skew(ret,60)，高阶矩反转。"""
    ret = close.pct_change(fill_method=None)
    return -ret.rolling(60).skew()


def factor_drawdown_120(close):
    """120 日最深回撤，回撤越浅分越高。"""
    running_max = close.cummax()
    dd = close / running_max - 1.0
    return dd.rolling(120, min_periods=1).min()


def factor_mfi(high, low, close, volume):
    """14 日资金流量指标，用 volume 替代 amount（与本地一致）。"""
    typical = (high + low + close) / 3.0
    flow = typical.diff() * volume
    pos_flow = flow.where(flow > 0.0, 0.0).rolling(14).sum()
    neg_flow = (-flow.where(flow < 0.0, 0.0)).rolling(14).sum()
    denom = (pos_flow + neg_flow).replace(0.0, np.nan)
    return pos_flow / denom


def factor_psy20(close):
    """20 日上涨日占比。"""
    ret = close.pct_change(fill_method=None)
    return (ret > 0.0).where(ret.notna()).rolling(20).mean()


# 因子函数分发表（名称 -> 调用函数，入参为面板字段）
FACTOR_FUNCS = {
    "macd_hist": lambda p: factor_macd_hist(p["close"]),
    "skewness_60_reversal": lambda p: factor_skewness_60_reversal(p["close"]),
    "drawdown_120": lambda p: factor_drawdown_120(p["close"]),
    "mfi": lambda p: factor_mfi(p["high"], p["low"], p["close"], p["volume"]),
    "psy20": lambda p: factor_psy20(p["close"]),
}


# ---------------------------------------------------------------
# 横截面 min-max 归一化到 (eps, 1]
# ---------------------------------------------------------------
def _normalize_cross_section(df, eps=EPS):
    """按日（每行）min-max 到 (eps,1]。

    全 NaN 行保持 NaN；无区分度行有效格映射为 1.0；缺失格保持 NaN。
    """
    df = pd.DataFrame(df)
    if df.empty:
        return df
    mn = df.min(axis=1, skipna=True)
    mx = df.max(axis=1, skipna=True)
    span = mx - mn
    tiny = span < 1e-12

    result = df.sub(mn, axis=0).div(span.where(~tiny, 1.0), axis=0)
    result = result * (1 - eps) + eps
    result.loc[df.isna().all(axis=1)] = np.nan
    result.loc[tiny] = 1.0
    result[df.isna()] = np.nan
    return result


def _build_category_scores(panel):
    """返回 {category_key: category_score_series}。

    每个分类得分 = 内因子各自横截面归一化后按 (日,标) 忽略 NaN 求均值。
    """
    cat_scores = {}
    for cat_key, factor_names in CATEGORIES.items():
        normed = []
        for fname in factor_names:
            mat = FACTOR_FUNCS[fname](panel)
            if mat is None or mat.empty:
                continue
            normed.append(_normalize_cross_section(mat))
        if not normed:
            continue
        cat_score = category_score_from_matrices(normed)
        if cat_score is not None and not cat_score.empty:
            cat_scores[cat_key] = cat_score
    return cat_scores


def category_score_from_matrices(normed):
    """等权平均一组已归一化矩阵，忽略 NaN。"""
    arr = np.stack([m.values for m in normed], axis=2).astype(float)
    valid = ~np.isnan(arr)
    count = valid.sum(axis=2)
    with np.errstate(invalid="ignore"):
        mean_vals = np.where(valid, arr, 0.0).sum(axis=2) / np.where(count == 0, 1, count)
    mean_vals[count == 0] = np.nan
    return pd.DataFrame(mean_vals, index=normed[0].index, columns=normed[0].columns)


def eaa_composite(cat_scores, exponents, beta=BETA):
    """EAA：S = ( Π_k norm(cat_k)^α_k )^β，仅 α_k>0 参与。返回 DataFrame。"""
    keys = list(cat_scores.keys())
    contributing = [k for k in keys if exponents.get(k, 0.0) > 0]
    if not contributing:
        return None

    product = None
    for key in contributing:
        alpha = exponents[key]
        normed = _normalize_cross_section(cat_scores[key])
        term = normed ** alpha
        product = term if product is None else product.mul(term, fill_value=1.0)

    if product is None:
        return None
    return product ** beta


# ---------------------------------------------------------------
# 主流程：调仓
# ---------------------------------------------------------------
def market_open(context):
    g.rebalance_counter += 1
    # 相位对齐本地 core 引擎：每 REBALANCE_PERIOD(5) 日块「首日决策、次一交易日收盘成交」。
    # counter 从 0 起每个交易日 +1，当 counter % 5 == 2（即索引 1,6,11,...=块首日的下一交易日）
    # 触发调仓，信号用 previous_date(=块首日,索引0) 收盘、当日收盘成交 → 与本地 rebalance_day=0
    # + shift(2) 口径逐日对齐。切勿改回 counter%5==0(块尾 4,9,14)，那会是几乎整块的相位差。
    if g.rebalance_counter % REBALANCE_PERIOD != 2:
        return

    log.info("调仓: counter=%d current_dt=%s previous_date=%s",
             g.rebalance_counter, context.current_dt, context.previous_date)
    log.info("开始调仓 (EAA)")
    panel = _load_panel(context)
    if "close" not in panel:
        log.warn("无法获取行情数据，跳过本次调仓")
        return

    cat_scores = _build_category_scores(panel)
    if not cat_scores:
        log.warn("无有效分类得分，跳过本次调仓")
        return

    composite = eaa_composite(cat_scores, EXPONENTS, BETA)
    if composite is None or composite.empty:
        log.warn("合成为空，跳过本次调仓")
        return

    # 取最近一行（当前运行日的前一交易日）作为因子信号
    latest = composite.iloc[-1].dropna()
    # EAA 合成分恒为正（min-max 到 (eps,1]），只保留正分（与本地 ScoreWeighted 一致）
    latest = latest[latest > 0.0]
    if len(latest) < TOP_N:
        log.warn("有效标的少于 top_n，跳过本次调仓")
        return

    # EAA：按合成分从高到低取 top_n，权重 ∝ 合成分（归一化到和为 1）
    ranked = latest.sort_values(ascending=False)
    selected = ranked.head(TOP_N)
    weights = (selected / selected.sum()).to_dict()

    _rebalance(context, weights)


def _rebalance(context, target_weights):
    """调到目标权重；买入金额按剩余可用现金封顶（预留 0.5% 缓冲）避免资金不足拒单。

    嵌套循环顺序：先平掉不在目标里的旧仓（释放资金），再逐笔买入目标标的。
    """
    target_set = set(target_weights.keys())
    current = list(context.portfolio.positions.keys())

    # 一、先卖出不在目标持仓中的标的，释放资金
    for sec in current:
        if sec not in target_set and context.portfolio.positions[sec].total_amount > 0:
            order_target_value(sec, 0)

    # 二、买入目标标的：金额 = 目标市值，但以剩余可用现金 * 0.995 封顶
    for sec, w in target_weights.items():
        target_value = context.portfolio.total_value * w
        available = context.portfolio.available_cash
        # 目标金额超过可用现金（含缓冲）时，压到可用现金以内，避免资金不足拒单
        order_value = min(target_value, available * 0.995)
        if order_value > 100:
            order_target_value(sec, order_value)