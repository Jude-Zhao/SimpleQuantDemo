# research 模块重构：独立因子池（协议一致）+ FAA/EAA 合成 + bt 开源回测

日期：2026-08-09
状态：设计中
范围：重构 `research/`，建立**独立的研究因子池**（与 web 生产因子池解耦、仅共享结构协议），统一用 FAA/EAA 合成，回测引擎切换为轻量开源库 **bt (bt.portfolio)**。webapp 保持现状不动。

## 1. 背景与问题

当前 `research/` 是项目最早遗留的研究入口，存在多处与现代体系脱节：

| 维度 | research（现状） | 期望 |
|------|------------------|------|
| 因子 | 硬编码 `MomentumFactor(5)` / `VolatilityFactor(20)` | **独立的 research 因子池**，可自定义/实验，遵循 `FactorBuilder` 协议 |
| 因子协议 | 与 core 一致 | 保持 `FactorBuilder`（`build → date × sec` 矩阵），**与 core/web 协议一致**，研究好的因子可直接迁移上线 |
| 合成 | `ICIRWeightedSynthesizer` + 共线性筛选 | 统一到 **FAA / EAA** |
| 回测 | 自研 `core.backtest.engine.run_backtest` | 切换为 **bt** |
| 绩效 | quantstats | 继续 quantstats（统一口径） |

具体问题：

1. **因子池不分离**：现状 research 硬编码两个因子，既没有独立研究空间，也没有遵循插件化协议。
2. **缺迁移通道**：research 研究出的新因子，没有"协议一致 → 迁移到 `core/factors/builtin` + `factors.yaml` 上线 web"的清晰路径。
3. **合成分叉**：research 用 ICIR 半衰期合成，web 用 FAA/EAA，两者不可比。
4. **回测负担**：自研引擎编排（`run_research` 单函数约 200 行）需持续维护，用户希望用开源框架。
5. **依赖方向错误**：FAA/EAA 纯函数位于 `webapp/`，应下沉 `core/`，避免 `research → webapp` 反向依赖。

## 2. 目标

1. **同数据源**：research 用 `core.data.SqliteDataSource`（与 webapp 同库 `data/simple_quant.db`），不动。
2. **因子协议一致、池子解耦**：research 建独立因子池，自定义因子遵循 `core.factors.base.FactorBuilder` 协议（`build → date × sec`），与 web 生产池**不共用一套因子**，但结构协议完全一致，研究后可直接迁移上线。
3. **可引用内置因子**：research 因子池允许显式复用 `core.factors.builtin` 的内置因子（动量/波动/反转）作为研究起点。
4. **统一合成**：research 策略合成统一到 FAA / EAA（纯函数下沉 core，两边复用）。
5. **开源回测**：回测引擎切换为 **bt**，**权重驱动**接入（research 用 core.optimization 算目标权重，bt 做持仓落地 + 净值 + 换手 + 绩效）。
6. **因子评价保留**：IC / RankIC / ICIR / 共线性 / 相关性保留为 research 的**独立因子研究产出**（`core.analysis`），用于因子挑选，不直接驱动组合。
7. **绩效指标统一**：research 绩效统一用 quantstats；webapp 按"仅 research 范围"不动，文档注明口径差异。
8. **新增依赖**：引入 `bt`。

## 3. 关键设计

### 3.1 因子协议与注册隔离

- **协议**：research 自定义因子继承 `core.factors.base.FactorBuilder`，实现 `build(price_data, macro_data, universe) -> DataFrame(date × sec)` 与 `name` 属性。输出结构与 core/web 因子完全一致。
- **注册隔离**：research 因子**不注册进** core 全局 registry（避免污染 web 因子注册表）。research 用**自己的 registry**（`research/factors/registry.py`）或直接清单加载。
- **迁移路径**：研究确认有效的因子 → 将类文件移入 `core/factors/builtin/<新类>/` → 在 `core/factors/builtin/factors.yaml` 分类登记 → research 清单改为引用 core 类型（或删除）。协议一致使迁移零改造成本。

### 3.2 research 因子池结构

```
research/
├── factors/                    # research 独立因子池
│   ├── __init__.py
│   ├── registry.py             # research 因子注册机制（可选，或直接清单）
│   └── <研究因子模块>.py         # 自定义因子，继承 core FactorBuilder
├── factor_config.yaml          # research 因子清单与分组（格式对齐 core factors.yaml）
├── main.py
├── config.py
├── bt_engine.py
└── backtest.py
```

- **因子清单**：`research/factor_config.yaml` 声明研究用因子实例与分组，**格式与 `core/factors/builtin/factors.yaml` 对齐**（`categories → key/display_name/factors[]`，每因子 `name + params`），便于研究后把分组整体迁到 core。
- **名称解析**：research 解析因子时，先查 research registry，查不到再 fallback 到 core registry（`get_factor_class`），从而既能用自定义研究因子，也能复用内置因子。
- **分组数据结构**：复用 `core.factors.config.FactorCategory` / `FactorInstance`，仅加载源换成 research 自己的 yaml。

### 3.3 FAA/EAA 下沉 core（纯函数与因子来源解耦）

将 `webapp/services/eaa_faa.py` 的纯函数迁移到 `core/synthesis/faa_eaa.py`：

```python
# core/synthesis/faa_eaa.py（新增）
EPS = 0.01

def normalize_cross_section(df, eps=EPS) -> pd.DataFrame: ...
def build_category_factors(price_data, universe, category, resolver=None) -> list[pd.DataFrame]: ...
def build_category_scores(price_data, universe, categories, resolver=None) -> dict[str, pd.DataFrame]: ...
def faa_composite(category_scores, class_weights, eps=EPS) -> pd.DataFrame: ...
def eaa_composite(category_scores, exponents, beta=1.0, eps=EPS) -> pd.DataFrame: ...
```

- `build_category_factors` / `build_category_scores` 增加可选 `resolver` 参数（callable: `name+params → FactorBuilder`），默认用 core registry；**research 传入自己的 resolver**（research registry + core 兜底），实现"因子来源解耦、合成函数复用"。
- **webapp 兼容**：`webapp/services/eaa_faa.py` 改为 re-export core（`from core.synthesis.faa_eaa import *`），`strategy_service.py` 现有 import 不变。
- 合成纯函数不关心因子来自哪个池，只吃"分组得分矩阵"。

### 3.4 research 因子构建与合成

`research/main.py` 流程：

```python
from core.factors.config import FactorCategory
from core.synthesis.faa_eaa import build_category_scores, faa_composite, eaa_composite
from research.factors.registry import resolver   # research+core 兜底

categories = load_research_categories()          # 读 research/factor_config.yaml → FactorCategory
category_scores = build_category_scores(
    price_data, universe, categories, resolver=resolver
)
composite = faa_composite(category_scores, class_weights)   # 或 eaa_composite(...)
```

- 因子来自 research 池（自定义 + 可引用内置），合成用 FAA/EAA 纯函数 → 合成逻辑与 web 完全同源，因子池独立。

### 3.5 回测切换为 bt（权重驱动）

**原则**：research 负责"因子 → composite 得分 → 调仓日目标权重"；bt 负责"目标权重 → 持仓落地 + 净值 + 换手 + 绩效"。

```
price_data ─▶ 因子构建/合成（research + core）──▶ composite 得分（date × sec）
                                                        │
                                                        ▼
            core.optimization（EqualWeight / ScoreWeighted）在调仓日算目标权重，ffill 全交易日
                                                        │
                                                        ▼
            bt.Strategy（RunWeekly/Monthly + SelectAll + WeighTarget + Rebalance）
                                                        │
                                                        ▼
                    净值 / 换手 / 绩效
```

- **目标权重矩阵**：research 用 `core.optimization.EqualWeightOptimizer` / `ScoreWeightedOptimizer`，在 `core.calendar.generate_rebalance_dates` 调仓日按 composite 得分转权重，ffill 到全部交易日。
- **bt 整合**：新增 `research/bt_engine.py`，封装 `(price_data, target_weights, rebalance_freq)` 为 bt 回测：

```python
# research/bt_engine.py（示意）
import bt

def run_bt_backtest(price_data, target_weights, rebalance_freq="monthly", name="research"):
    close = pivot_close(price_data)          # date × sec 收盘价
    data = bt.Node("close", close)
    strategy = bt.Strategy(
        name,
        algos=[
            bt.algos.RunWeekly() if rebalance_freq == "weekly" else bt.algos.RunMonthly(),
            bt.algos.SelectAll(),
            bt.algos.WeighTarget(target_weights),
            bt.algos.Rebalance(),
        ],
    )
    return bt.run(bt.Backtest(strategy, data))
```

- 绩效指标以 quantstats 为准（与 web 口径可比）；`bt.Result` 提供净值/换手/绩效。
- `research/backtest.py` 保持 re-export 语义，适配 bt 结果（净值、权重、换手 Series）。
- > 说明：bt 具体 API（`WeighTarget` 权重格式、`Node` 行情对齐、`RunWeekly/RunMonthly` 触发）在实现阶段用冒烟脚本验证后固化，本设计只定结构与数据流。

### 3.6 research 配置（ResearchConfig 调整）

`research/config.py`：

| 字段 | 现状 | 调整 |
|------|------|------|
| `db_path` / `output_dir` / `start_date` / `end_date` | 保留 | 不变 |
| `momentum_window` / `volatility_window` | 硬编码因子参数 | **删除**，因子改由 `research/factor_config.yaml` 驱动 |
| `forward_return_horizon` / `ic_*` / `icir_*` / `half_life_periods` / `collinearity_threshold` | 单一固定 | 保留，用于**因子评价分析**，不驱动组合 |
| `strategy_type` | 无 | **新增**：`"faa" | "eaa"`，默认 `faa` |
| `strategy_params` | 无 | **新增**：FAA `class_weights`、EAA `exponents`/`beta`、`top_n`、`rebalance_freq` |
| `weight_mode` | 无 | 由 strategy_type 派生（FAA=equal，EAA=score） |

### 3.7 CLI 接口

`python -m research.main` 扩展参数：

```text
--db-path --start-date --end-date --output-dir        # 保留
--strategy {faa,eaa}                                   # 默认 faa
--top-n {3,5,7,9}                                      # 默认 5
--rebalance-freq {weekly,monthly}                      # 默认 monthly（与 webapp 一致）
```

类权重 / 指数 / β 等复杂参数不暴露 CLI，用 `research/config.py` 配置。

### 3.8 输出格式

保留 research 现有 CSV + PNG 输出，新增 bt 净值图：

| 文件 | 说明 |
|------|------|
| `summary.csv` | 绩效指标（quantstats + 组合特有） |
| `equity_curve.csv` | 每日策略净值（取自 bt 结果） |
| `weights.csv` | 每日持仓权重 |
| `factor_stats.csv` | 因子评价：IC / RankIC / ICIR（独立分析，不驱动组合） |
| `synthesized_scores.csv` | FAA/EAA 合成得分 |
| `warnings.txt` | 共线性警告 / 被剔除因子 |
| `equity_curve.png` | 净值曲线（bt `plot()` 或保留现有 `visualization/`） |
| `factor_stats.png` | IC / ICIR 图 |
| `latest_weights.png` | 最新持仓权重图 |

### 3.9 因子评价独立分析

`research.main` 跑策略**之前**独立产出因子评价（保留 `core.analysis` 调用链）：

- IC / RankIC / ICIR（`calculate_factor_ic` / `calculate_rank_ic` / `calculate_icir`）
- 共线性（`analyze_collinearity`，`mode="warn"`，仅告警不选样）
- 相关性矩阵（`calculate_factor_correlation_matrix`）

输出到 `factor_stats.csv` / `warnings.txt`，作为研究参考，**不参与** FAA/EAA 组合构建。

### 3.10 依赖

`pyproject.toml` 的 `[project] dependencies` 增加：

```toml
"bt>=1.1.0",
```

## 4. 影响文件

| 文件 | 改动 |
|---|---|
| `core/synthesis/faa_eaa.py` | **新增**：FAA/EAA 纯函数（自 webapp 下沉，`build_category_scores` 支持 `resolver`） |
| `core/synthesis/__init__.py` | 导出 FAA/EAA 函数 |
| `webapp/services/eaa_faa.py` | 改为 re-export core（删除重复实现） |
| `research/factors/` | **新增**：research 独立因子池（registry + 自定义因子示例） |
| `research/factor_config.yaml` | **新增**：research 因子清单与分组（对齐 core yaml 格式） |
| `research/main.py` | 重写：因子走 research 池、合成走 FAA/EAA、因子评价独立分析、调 bt 回测 |
| `research/bt_engine.py` | **新增**：bt 回测封装（权重驱动） |
| `research/backtest.py` | 适配：bt 结果适配层 |
| `research/config.py` | `ResearchConfig` 改造（strategy_type / strategy_params） |
| `research/visualization/` | 视情况保留/裁剪，bt `plot()` 作净值图 |
| `pyproject.toml` | 增加 `bt` 依赖 |
| `tests/test_research_*.py` | 更新/新增（见 §6） |

## 5. 明确不做

- **webapp 不动**：`strategy_service.py`、`core.backtest.engine`、`_compute_metrics` 均保持现状。research 与 webapp 的绩效口径差异在文档（用户手册）中注明。
- **不删除 registry / `get_factor_class` / `factors.yaml`**：继续作为 core/web 因子实现与分类来源。
- **不把 research 自定义因子注册进 core 全局 registry**：结构协议一致，但注册空间隔离。
- **不删除 `core.backtest.engine`**：webapp 仍使用；`EqualWeightOptimizer` / `ScoreWeightedOptimizer` 继续被 core.backtest 与 research 共用。
- **不删除 ICIR 合成能力**：`ICIRWeightedSynthesizer` / `analyze_collinearity(mode="select")` 保留为研究分析接口，但 research 主流程不再用它驱动组合。
- **不在本阶段引入参数扫描**：bt `bt.optimize` 能力记录为后续可选，不在本次落地。

## 6. 测试计划

- **单元测试**：
  - `core/synthesis/faa_eaa`：下沉后函数行为与迁移前一致（对拍或断言核心输出）；`resolver` 参数可解析 custom 因子。
  - `research/factors`：research registry 能解析自定义因子，且 fallback 到 core 内置因子；不污染 core 全局 registry。
  - `research/config`：`ResearchConfig` 新旧字段默认值、strategy 派生 weight_mode。
  - `research/bt_engine`：给定权重/得分，返回净值、权重、换手长度正确；调仓频率生效。
- **集成测试**：
  - `test_research_pipeline`：research（FAA/EAA）用 research 因子池可跑通全流程；引用内置因子时与 core 因子结果一致。
  - 因子评价产出（factor_stats / warnings）非空。
- **迁移示例测试**：新增一个 research 自定义因子 → 按协议迁移到 core 后，FAA/EAA 结果一致（验证"协议一致、迁移零成本"）。
- **回归**：全量 `pytest` 通过；确认 webapp 赛道（`core.backtest.engine`、`strategy_service`）行为不变。