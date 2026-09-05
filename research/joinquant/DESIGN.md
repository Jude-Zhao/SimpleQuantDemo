# 聚宽 EAA / FAA 策略迁移设计文档

日期：2026-08-23
状态：待确认
范围：将本地 SimpleQuantDemo 的 EAA / FAA 两个 ETF 多因子策略迁移到聚宽（JoinQuant），可在聚宽平台实盘/回测运行。

---

## 1. 背景与目标

本地项目将 EAA / FAA 两个自定义动量分类合成策略用于 30 只 ETF。
本次目标是在聚宽上复现这两个策略，生成两个自包含可运行的聚宽策略文件：

- `research/joinquant/faa_strategy.py` —— FAA（等权 Top-N）
- `research/joinquant/eaa_strategy.py` —— EAA（分数加权 Top-N）

要求与本地回测口径尽量一致（因子、归一化、合成、参数、标的池）。

## 2. 已确认决策

| 项目 | 决策 |
|------|------|
| 标的池 | 数据库 `simple_quant.db` 中 `is_active=1` 的 **30 只 ETF**（含 159915.SZ，7 深 + 23 沪） |
| 调仓频率 | **5d（每 5 个交易日）**，与本地 `"5d"` 一致 |
| 基准指数 | 中证 500（`000905.XSHG`），`set_benchmark` |
| 交易品种 | ETF，`set_order_cost(type='fund')` |

## 3. 标的池代码转换（本地 .SH/.SZ → 聚宽 .XSHG/.XSHE）

| 本地 | 聚宽 | 本地 | 聚宽 |
|------|------|------|------|
| 159915.SZ | 159915.XSHE | 512580.SH | 512580.XSHG |
| 159928.SZ | 159928.XSHE | 512680.SH | 512680.XSHG |
| 159929.SZ | 159929.XSHE | 512690.SH | 512690.XSHG |
| 159939.SZ | 159939.XSHE | 512720.SH | 512720.XSHG |
| 159941.SZ | 159941.XSHE | 512800.SH | 512800.XSHG |
| 159967.SZ | 159967.XSHE | 512980.SH | 512980.XSHG |
| 159985.SZ | 159985.XSHE | 513500.SH | 513500.XSHG |
| 510300.SH | 510300.XSHG | 513660.SH | 513660.XSHG |
| 510500.SH | 510500.XSHG | 513770.SH | 513770.XSHG |
| 510880.SH | 510880.XSHG | 513880.SH | 513880.XSHG |
| 511010.SH | 511010.XSHG | 515880.SH | 515880.XSHG |
| 512000.SH | 512000.XSHG | 518880.SH | 518880.XSHG |
| 512040.SH | 512040.XSHG | 588000.SH | 588000.XSHG |
| 512100.SH | 512100.XSHG | | |
| 512200.SH | 512200.XSHG | | |
| 512400.SH | 512400.XSHG | | |
| 512480.SH | 512480.XSHG | | |

共 30 只。代码清单统一以字符串常量 `UNIVERSE` 放在每个策略文件顶部。

## 4. 因子清单与聚宽复现公式（生产 5 因子）

> 项目有两套因子配置：**生产**（`core/factors/builtin/factors.yaml`，Web 看板/策略实际使用，5 个因子）与**研究**（`research/factor_config.yaml`，research 区因子验证用，10 个）。聚宽策略迁移的是**生产 5 因子**。

所有因子方向均 `positive`（值越大得分越高），无需取反。
因子计算使用**后复权价**（`get_price(..., fq='post')`）以对齐本地 hfq，与订单的实际价格（`use_real_price=True`）解耦。

| 分类 | 因子 | 本地公式 | 窗口 |
|------|------|----------|------|
| momentum | macd_hist | `(DIF-DEA)/close`；DIF=EMA12-EMA26，DEA=EMA9(DIF)，EWM adjust=False | 长窗 EMA |
| reversal | skewness_60_reversal | `-skew(ret,60)` | 60 |
| volatility | drawdown_120 | `min(close/cummax-1,120)` 滚动120日最小 | 120 |
| volume | mfi | `pos_flow/(pos_flow+neg_flow)`；flow=TP.diff()*volume，TP=(H+L+C)/3，14日 | 14 |
| volume | psy20 | `mean(ret>0,20)` | 20 |

> 注：MFI 本地用 `volume` 替代 `amount`（本地 ETF 库 amount 为 0）。聚宽有真实成交额，
> 但为与本地口径严格一致，聚宽版本**默认仍用 volume**；如后续想用聚宽 money，只需改一行。

### 因子在聚宽中的实现要点
- 各股价字段（open/high/low/close/volume/money）按大纲获得后 `pivot` 成横截面
  DataFrame（index=交易日，columns=标的），与本地 `pivot_price_field` 一致。
- `EMA12/EMA26/EMA9` 用 pandas `ewm(span=, adjust=False)`。
- `rolling(60).skew()`、`rolling.std(ddof= ?)`：本地 `Series.std()` 默认 `ddof=1`，聚宽相同 pandas 默认，保持一致。
- `drawdown_120` 用 `rolling(120, min_periods=... )`。本地为 `.rolling(120, min_periods=1).min()`。
- plrc24 滑动 OLS 斜率用 `rolling(24).apply(slope / mean(abs))`。

## 5. 归一化与分类合成（与本地 core/synthesis 一致）

### 5.1 横截面 min-max 归一化到 (eps, 1]
对每一个因子矩阵，**按日（每行）**做 min-max：
```
norm = (x - min)/ (max - min) * (1 - eps) + eps
```
- eps = 0.01（本地 EPS）。
- 当日某行全部数值相同（无法区分）→ 该行有效格映射为 1.0。
- 缺失值保持 NaN，不参与，也不被赋予 0（避免给无行情标的错误低分）。
- min-max 在**横截面**（同一日全部标的）进行，因此标的池必须固定为上面 30 只。

### 5.2 分类内等权平均
每个分类 = 其下各因子先各自横截面归一化，再对非 NaN 的格子取均值（忽略 NaN）。

### 5.3 分类级 min-max 归一化
对每个分类得分矩阵再做 5.1 的横截面 min-max 归一化。

### 5.4 合成
- **FAA**：`L = Σ wₖ · norm(catₖ)`；权重归一化到和为 1。
  默认 `w = {momentum:0.20, reversal:0.30, volatility:0.25, volume:0.25}`。
- **EAA**：`S = ( Π norm(catₖ)^αₖ )^β`，仅权重>0 的分类参与。
  默认 `α = {momentum:0.5, reversal:1.0, volatility:1.0, volume:1.25}`，`β = 0.5`。

### 5.5 选股与权重
- 各调仓日对全部 30 只的合成分排序。
- 取合成分最高的 **top_n = 5** 只。
- **FAA**：5 只等权，`weight = 1/5`。
- **EAA**：5 只按分数成比例加权，`weight ∝ norm(合成分)`（线性，归一化到和为 1）。

## 6. 交易设定（聚宽）

```
set_benchmark('000905.XSHG')
set_option('use_real_price', True)
set_order_cost(OrderCost(open_tax=0, close_tax=0,
    open_commission=0.00025, close_commission=0.00025,
    min_commission=0), type='fund')   # ETF
set_slippage(FixedSlippage(0))         # 与本地无滑点设定对齐
```

- 调仓在收盘执行 `run_daily(market_open, time='close')`，以当日收盘价成交（对齐本地 web `shift(2)`/收盘成交口径）。
- 调仓频率 5d：维护 `context.counter`，每交易日 `+=1`，当 `counter % 5 == 1` 时调仓（首个交易日即调仓）。
- 因子使用 **昨日（`context.previous_date`）** 收盘及更早数据计算，当日收盘按该信号调仓，**无前视**（同模板「上一交易日因子值」口径）。
- 调仓：先卖不在目标持仓的，再按目标权重买入；用 `order_target_value`。

## 7. 防前视 / 数据窗口
- 每个调仓日对每只标的拉取 `get_price(sec, start_date=T_前约130交易日, end_date=context.previous_date, fq='post', fields=[...])`，保证因子窗口（最长 120 日）有完整回看。
- 取最近一行即 T-1 日的因子值，当日收盘据此成交。
- 复权用 `fq='post'`（后复权）对齐本地 hfq。

## 8. 文件结构
```
research/joinquant/
├── DESIGN.md            <- 本文档
├── faa_strategy.py      <- FAA 策略（自包含，可独立上传聚宽）
└── eaa_strategy.py      <- EAA 策略（自包含，可独立上传聚宽）
```
两个文件**自包含**（不互相 import），各自内置因子函数、归一化、合成与交易逻辑，
仅末端合成与权重模式不同。公共部分（因子/归一化/取数）在两文件间保持逻辑一致。

## 9. 运行方式（聚宽）
1. 在聚宽研究/策略里上传对应 `.py`。
2. 回测区间、初始资金可在聚宽界面设置。
3. 基准已在 `initialize` 中设为中证 500。

## 10. 待核验项
- 聚宽 30 只 ETF 代码在回测中是否全部可获取数据 / 可交易（个别可能停牌或无上市前数据）。
- `fq='post'` 与 `use_real_price` 组合下复权价/成交价是否如预期。
- 在聚宽回测中抽样比对 1~2 个调仓日的因子值与本地 DB 计算值是否吻合（可选，投入较高）。