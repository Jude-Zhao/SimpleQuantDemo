# 本地因子 + ETF BL-lite 15 因子按分类对比验证设计

> 日期：2026-08-15（v2，更新为「本地 + 文档因子按分类对比验证」）
> 上游文档：`data_example/实盘15因子汇总.md`（上游 `qmt_client_strategies` 项目，独立于本仓库）
> 本仓库工作流：`research/` 验证 → 表现好的移植到 `core/factors/builtin/` + Web 看板

---

## 1. 背景与目标

上游 `etf_bl_lite_backtest_v2.py` 锁定 **4 组 15 个实盘因子**，公式与 `compute_atomic_factors` 1:1 对齐。
本仓库自身已有若干内置因子（momentum / volatility / reversal）。

本任务目标：**把本地已有因子和文档 15 因子按分类放一起，在 `research/` 分别回测、对比每个分类下谁的效果更好**，
把同分类下表现好的因子移植到生产 `core/factors/builtin/` + Web 看板。

## 2. 范围

**做**
- 在 research 池实现文档 15 个因子（`@register_factor`），连同本地已有因子一起按分类登记。
- 跑因子 IC 研究（`factor_stats.csv`），按分类对比本地 vs 文档因子。
- 依据研究结果筛选表现好的因子，移植到 `core`。
- 移植后跑全量测试 + 分类配置加载校验。

**不做**（本次明确排除）
- 不做 regime（risk_on/balanced/defensive）三档权重。
- 不改策略合成逻辑、不改分类权重默认值。
- 不删除本地已有因子，新因子与旧因子并存。

## 3. 因子分类与分组映射

沿用系统现有分类。文档 4 组与现有分类语义一一对应：

| 文档分组 | 现有分类 | 说明 |
|---|---|---|
| trend_momentum（5） | `momentum`（动量） | 短期延续 / 趋势 |
| reversal_crowding（4） | `reversal`（反转） | 反转 / 拥挤控制 |
| risk_quality（3） | `volatility`（波动） | 防御 / 风险质量 |
| liquidity（3） | `volume`（量能） | 资金流方向 |

### 3.1 本地已有因子（对比基准）

| 分类 | 因子 | 说明 |
|---|---|---|
| momentum | `momentum_20` / `momentum_60` / `momentum_120` | 系统内置动量 |
| volatility | `volatility_20` / `volatility_60` | 系统内置波动（已取反） |
| reversal | `reversal_5` / `reversal_20` | 系统内置反转（已取反） |
| volume | （空） | 本地无量能因子 |
| other | `price_position_20` | research 研究示例（价格位置，可选参考，默认不参与对比） |

### 3.2 文档 15 因子（按分类归入）

- **momentum**：`macd_hist` / `plrc24` / `risk_adj_momentum_120` / `aroon_diff` / `momentum_10`
- **reversal**：`reversal_bias5` / `momentum_60_reversal` / `ma60_slope_reversal` / `skewness_60_reversal`
- **volatility**：`low_vol_60` / `low_downside_vol_60` / `drawdown_120`
- **volume**：`mfi` / `psy20` / `money_flow_20`

> 每个分类下本地因子与文档因子混合，`factor_stats.csv` 会列出全部因子，按分类对比即可。

## 4. 15 因子规格（公式 / 方向 / 参数）

所有因子按文档代码实现，输出均为「高分 = 越好」（direction=positive）。
因子用 `pivot_price_field` 取 `close/high/low/volume` 字段。

### 4.1 momentum（动量 / 趋势，5 个）

| registry_name | name | 公式（纯文本） |
|---|---|---|
| `macd_hist` | macd_hist | (DIF-DEA)/close；DIF=EMA12-EMA26，DEA=EMA9(DIF)，ewm(adjust=False) |
| `plrc24` | plrc24 | 24 日收盘价 OLS 斜率 / 24 日均价绝对值 |
| `risk_adj_momentum_120` | risk_adj_momentum_120 | (close/close.shift(120)-1) / (std60(ret)*sqrt(252)) |
| `aroon_diff` | aroon_diff | (argmax+1)/25 - (argmin+1)/25，25 日窗口 |
| `momentum_10` | momentum_10 | close/close.shift(10)-1 |

### 4.2 reversal（反转，4 个）

| registry_name | name | 公式 |
|---|---|---|
| `reversal_bias5` | reversal_bias5 | -(close/MA5 - 1) |
| `momentum_60_reversal` | momentum_60_reversal | -(close/close.shift(60)-1) |
| `ma60_slope_reversal` | ma60_slope_reversal | -(MA60_t/MA60.shift(20)-1) |
| `skewness_60_reversal` | skewness_60_reversal | -skew60(ret) |

> 四者均已取负，输出「越大越好」。

### 4.3 volatility（波动 / 风险质量，3 个）

| registry_name | name | 公式 |
|---|---|---|
| `low_vol_60` | low_vol_60 | -std60(ret)（clip 下限 1e-8） |
| `low_downside_vol_60` | low_downside_vol_60 | -std60(min(ret,0))（clip 1e-8） |
| `drawdown_120` | drawdown_120 | min(close.tail(120)/cummax - 1)（回撤越浅分越高） |

### 4.4 volume（量能 / 资金流，3 个）

| registry_name | name | 公式 | 数据口径 |
|---|---|---|---|
| `mfi` | mfi | 14 日正负资金流比；TP=(H+L+C)/3，flow=TP.diff()***volume** | ⚠ amount→volume |
| `psy20` | psy20 | 20 日内上涨日占比 mean(ret>0) | close |
| `money_flow_20` | money_flow_20 | sum(ret***volume**,20)/sum(volume,20) | ⚠ amount→volume |

> **amount→volume 决策（用户 2026-08-15 确认）**：真实库 28 只激活 ETF 的 `amount` 字段全部为 0
> （baostock 源未填充），文档原口径 `mfi` / `money_flow_20` 用 `amount` 无法实现。改用 `volume` 替代：
> - `mfi` 分子分母都是比值，价格因素抵消，volume 与 amount 结果几乎一致；
> - `money_flow_20` 是加权收益，权重用 volume 对单只 ETF 自身影响很小；
> - 标准 MFI 本就用成交量（volume），本地 volume 数据完整。
> `psy20` 只用 close，不受影响。

## 5. 验证方案

### 5.1 实现位置
文档 15 因子写成**研究因子**，放 `research/factors/`，用 `research.factors.registry.register_factor` 注册。
按组组织文件（`etf15_momentum.py` / `etf15_reversal.py` / `etf15_risk.py` / `etf15_liquidity.py`）。
**不直接进 core**，先验证。

### 5.2 登记
在 `research/factor_config.yaml` 现有分类下，把本地因子 + 文档 15 因子全部登记（momentum/reversal/volatility/volume 各归其类）。
不新增分类、不删除现有分类。

### 5.3 口径与命令
- 数据：`data/simple_quant.db`（28 只激活 ETF，后复权 hfq）。
- 命令：`python -m research.main --start-date 2021-01-04 --data-start-date 2019-11-01`
  （默认 faa、monthly、top_n=5）。
- **回测/IC 起点 = 2021-01-04**（28 只标的 2021-01 起数据齐整）；数据加载从 2019-11-01 开始，
  为 120 日窗口因子提供预热历史。`research/main.py` 新增 `--data-start-date` 参数，
  分离「数据加载起点」与「评估/回测起点」（`ResearchConfig.data_start_date`，默认 None=同 start_date）。
- 评审依据：`research/output/backtest_results/factor_stats.csv` 中全部因子的 **IC / RankIC / ICIR**。
- IC 口径：系统默认 `forward_return_horizon=5`、`ic_min_observations=10`、`icir_window=20`。
  > 与文档 t+10 回测口径不同，属预期——验证的是「在本仓库数据上该因子是否有效、同类下谁更强」。

### 5.4 分类对比与移植判定
按分类对比本地 vs 文档因子，以 `factor_stats.csv` 为准，同时满足：
1. **RankIC 均值 > 0**（方向正确）；
2. **RankICIR 均值 > 0.15** 视为「可移植」，0.10–0.15 视为「边缘，观察」；
3. **方向一致**：IC 与 RankIC 同号，且与文档标注方向不冲突；
4. **共线性**：`warnings.txt` 中若某新因子与已有因子相关 > 0.7，评审时二选一。

> 具体阈值以实际数据为准，移植清单需用户确认。

## 6. 实施步骤（分阶段，逐步确认）

1. **Phase 1：实现研究因子**（research 池文档 15 因子 + factor_config.yaml 登记本地+文档全部因子）
2. **Phase 2：跑验证**（`python -m research.main`，产出 factor_stats.csv）
3. **Phase 3：评审**（按分类对比 IC/RankIC/ICIR，给用户确认移植清单）
4. **Phase 4：移植**（表现好的进 `core/factors/builtin/<name>/`，改 `register_factor("name")`，登记 `factors.yaml`）
5. **Phase 5：校验**（`factors.yaml` 加载校验 + `pytest -q` + 重启看板确认因子出现在列表）

## 7. 风险与注意

- **数据口径差异**：28 只池 vs 上游 29 只、IC horizon 5 vs t+10、无 L/S 回测。IC 数值不与文档完全一致，**同分类相对强弱**才是评审依据。
- **`amount` 全为 0**：`mfi` / `money_flow_20` 已改为 volume 口径（见 4.4，用户已确认）。
- **`skewness_60_reversal` 与 `momentum_60_reversal` 共线**：文档已提示，若相关 > 0.7 只保留一个。
- **`aroon_diff` 文档标注最弱**（RankICIR +0.19 / LS Sharpe -0.19）：大概率被评审淘汰，属预期。
- **`risk_quality` 组 LS 为负是设计意图**，但本次不做 regime，该组按普通因子标准评审。
- **不删除旧因子**：本地 momentum/volatility/reversal 保留，与文档因子并存对比。