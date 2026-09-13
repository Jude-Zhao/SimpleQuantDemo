# QWEN.md — SimpleQuantDemo

ETF 多因子轮动量化研究 + 策略回测 + Web 看板一体化项目。本文件是给未来交互的指令性上下文，覆盖项目目的、架构、运行命令与开发约定。

## 1. 项目概述

SimpleQuantDemo 是一个 **Python 量化研究演示项目**，核心是「因子研究 → 因子合成 → 组合优化 → 策略回测 → Web 看板」完整链路：

- **因子**：插件式因子库，因子以 `date × sec` 得分矩阵形式输出。
- **分析**：IC / RankIC / ICIR / 因子相关性（共线性）。
- **策略**：**FAA**（因子类权重线性加权）与 **EAA**（幂函数乘法组合）两种自定义动量策略（**非** Keller 论文原版公式）。
- **回测**：共享回测引擎 + `bt` 开源库引擎。
- **展示**：Web 看板（FastAPI + 静态前端 + ECharts）。

**技术栈**：Python ≥ 3.10 · FastAPI · SQLAlchemy · SQLite · pandas/numpy · `bt`（回测）· `quantstats`（绩效）· AkShare（数据源）。

**数据口径**：行情统一为 **后复权（hfq）**，唯一源腾讯 fqkline（经 AkShare），本地 SQLite 缓存。

## 2. 分层架构

项目刻意拆分「研究」与「生产」两层，两边因子注册表**相互隔离**：

| 层 | 目录 | 职责 |
|----|------|------|
| 生产核心 | `core/` | 因子库、分析、合成、优化、回测引擎、数据源 —— Web 看板直接依赖 |
| 研究工作区 | `research/` | 实验因子池 + 独立回测，做验证；通过后才移植到 `core/` |
| Web 看板 | `webapp/` | FastAPI API + SQLAlchemy ORM + Pydantic schema + 静态 SPA |
| 测试 | `tests/` | 全量 pytest |

**数据流**：外部数据源（AkShare/腾讯）→ 同步进 `data/simple_quant.db`（`etf_daily_bar` 等表）→ `research` 与 `webapp` 都从这张共享库读取。`research` 只读不写，数据同步统一走 Web 设置页。

## 3. 目录结构

```
core/
├── factors/        # 插件式因子库（base/registry/config + builtin/）
├── analysis/       # IC / RankIC / ICIR / 共线性
├── synthesis/      # 因子合成（faa_eaa.py 为核心）
├── optimization/   # 等权 / 得分加权 / 约束校验
├── backtest/       # 共享回测引擎（engine.py）
├── data/           # 数据源抽象 + AkShare(腾讯fqkline)/SQLite + default_universe.py
└── calendar.py     # 交易日历 + 调仓日生成
research/
├── main.py         # 一键研究+回测入口（python -m research.main）
├── config.py       # ResearchConfig（dataclass）
├── factor_config.yaml   # 研究因子清单（改这里最常用）
├── factors/        # 独立研究因子池（与 core 注册表隔离）
├── bt_engine.py    # bt 开源库回测引擎
├── backtest.py     # 兼容层：re-export core.backtest.engine
└── output/         # 回测结果输出（自动生成）
webapp/
├── main.py         # FastAPI app 装配
├── config.py       # 配置加载（YAML + 环境变量覆盖）
├── api/            # REST 路由（factors/market/macro/universe/strategies/...)
├── services/       # 业务层（strategy/universe/data/sync/factor/...）
├── models/         # SQLAlchemy ORM
├── schemas/        # Pydantic 模型
└── static/         # 前端 SPA（index.html + css/js/vendor）
config/webapp.yaml  # Web/数据源/同步/策略配置
docs/               # 需求文档、用户手册、开发环境约定、plan 记录
trading/            # 空目录（仅 __pycache__，遗留占位）
```

## 4. 关键架构决策与约定

### 4.1 核心模块导出

`core/` 各子包在 `__init__.py` 统一 re-export 公共接口，业务代码从子包顶层导入，例如：

```python
from core.analysis import calculate_factor_ic, calculate_rank_ic, calculate_icir, analyze_collinearity
from core.synthesis import build_category_scores, faa_composite, eaa_composite, normalize_cross_section
from core.optimization import EqualWeightOptimizer, ScoreWeightedOptimizer, validate_constraints
from core.data import SqliteDataSource
```

### 4.2 因子系统（插件式）

- 因子继承 `core.factors.base.FactorBuilder`，用 `@register_factor("name")` 装饰，靠 `registry.discover_factors()` 自动发现（递归扫描 `core.factors`，跳过 `base/registry/utils/exceptions/__init__` 及 `_` 开头模块）。
- **因子清单的唯一来源是 `core/factors/builtin/factors.yaml`**（Web 看板/首页/策略共用）。它有 5 个分类：`momentum/reversal/volatility/volume/other`。
- **`build()` 协议**：输入长表 `price_data`（列 `date/sec/open/high/low/close/volume/amount`）+ `macro_data` + `universe`；输出 `pd.DataFrame`，**index=date、columns=sec（顺序必须与 universe 完全一致）**。用 `utils.pivot_price_field()` 转矩阵、`utils.validate_factor_matrix()` 校验。
- **因子方向**在因子代码层处理（如波动率取反），策略层默认全部正向。
- **当前生产 5 因子**（2026-08 起）：`macd_hist`（动量）、`skewness_60_reversal`（反转）、`drawdown_120`（波动）、`mfi` + `psy20`（量能）。

### 4.3 研究 vs 生产因子池

- 研究因子用 `research.factors.registry.register_factor`，生产用 `core.factors.registry.register_factor`。两池隔离是**有意设计**。
- `research/factor_config.yaml` 的 `name` 解析顺序：**研究池 → core 内置**，名字写错在加载时报错（fail-fast）。
- 从研究移植到生产：把文件移到 `core/factors/builtin/<因子名>/`，改注册装饰器为 `@register_factor("名字")`，在 `factors.yaml` 对应分类登记，最后删掉研究池旧文件。协议（`FactorBuilder.build`）原样保留。

### 4.4 策略（FAA / EAA，自定义公式）

两个策略共用同一前端管线：构建因子矩阵 → 逐因子截面 min-max 归一化到 `(eps, 1]`（`EPS=0.01`）→ 类内因子等权合成类得分 → 类间合成 → Top N 选股。

- **FAA**：`Lᵢ = Σₖ wₖ · norm(catₖ)`，类权重归一化到 sum=1，选 Top N 后**组内等权**（`faa_composite`）。
- **EAA**：`Sᵢ = ( Πₖ norm(catₖ)^αₖ )^β`，仅 αₖ>0 的类参与累乘，选 Top N 后**按得分加权**（`eaa_composite`）。

公式用纯文本（`norm`=截面 min-max 归一化，`w`/`α` 为类权重/缩放系数，`β` 为整体缩放）。核心实现在 `core/synthesis/faa_eaa.py`，`research` 与 `webapp` 共用同一套纯函数（通过 `resolver` 注入因子源）。

**默认参数**（来自 `research/tune_strategy_params.py` 网格搜索 2021–2026）：
- FAA 类权重：动量 0.20 / 反转 0.30 / 波动 0.25 / 量能 0.25
- EAA：α 0.5 / 1.0 / 1.0 / 1.25，β = 0.5
- 默认 `top_n=5`，调仓频率 `5d`（每 5 个交易日）

> 默认参数需在 **webapp（`strategy_service._build_meta`）与 research（`config.py`）两处**同步修改。

### 4.5 两个回测引擎

| 引擎 | 位置 | 使用方 | 防止前视 |
|------|------|--------|----------|
| 共享回测 | `core/backtest/engine.py`（`research/backtest.py` 是其 re-export） | **webapp 策略执行** | 信号价≠成交价 |
| bt 开源库 | `research/bt_engine.py` | **research 主流水线** | 决策日 T，T+1 收盘执行（`RunOnDate`） |

注意 `webapp/services/strategy_service.py` 通过 `from research.backtest import ...` 使用的是 `core.backtest.engine`，**不是** bt 库引擎。

### 4.6 调仓频率

`core/calendar.py` 的 `RebalanceFrequency = Literal["weekly", "monthly", "5d"]`。`"5d"` = 每连续 5 个交易日调仓一次（`np.arange(len(dates)) // 5` 分块）。原先的 monthly 已改为默认 5d，贯穿 calendar / backtest / webapp / research。

### 4.7 默认标的池

`core/data/default_universe.py` 是**唯一常量源**，现为 30 只激活 ETF（宽基/行业/跨境/商品/债券/策略）。它只是**兜底种子**：live 当前标的池始终来自数据库 `universe_items`（`is_active=1`）。增加/移除标的后需同步这份列表。

## 5. 数据库与数据源

- **SQLite 单文件**：`data/simple_quant.db`（Web 与 research 共享）。连接串在 `config/webapp.yaml` 的 `database.url`，可用环境变量 `DATABASE_URL` 覆盖（见 `.env.example`）。
- 主要表：`etf_daily_bar`、`macro_daily`（宏观）、`universe_items`（标的池）、`strategy_runs`（策略运行记录）、classification/constraint 相关表。
- **后复权**：`etf_daily_bar.close` 是后复权价；系统不保存真实价/复权因子（`adj_factor` 列已于 2026-09-13 删除），如需真实价须另行回补未复权序列换算。
- 数据源：唯一源 AkShare（腾讯 fqkline 后复权），**无备用源**（baostock 对 ETF 的复权参数静默无效、历史仅约 8 个月，已于 2026-09-12 移除）。`config/webapp.yaml` 配 `primary/cache`。
- **腾讯源加固**：浏览器 UA + Referer；全局请求间隔 0.75~1.25s 均匀抖动（可配 `datasource.tencent_min_interval/tencent_max_interval`）；2 年分段拉取（单请求上限 640 行）；指数退避重试（1/2/4s，仅网络异常/坏 JSON/其他非 200）；WAF 拦截（HTTP 501/拦截页）**不重试**立即抛 `TencentSourceError`；连续 3 只源级失败熔断、剩余标的记"未尝试"（`source_break_threshold`），失败原因透传任务结果。
- 同步入口：Web 设置页手动触发，走 `webapp/services/sync_service.py`（后台线程 + 进度条 + 逐标的明细/失败原因展示）。

## 6. 环境搭建与运行

### 6.1 安装依赖

```bash
pip install -e ".[dev]"    # 含 pytest
```

开发环境约定见 `docs/开发环境.md`：默认使用 Conda 环境 `QuantitativeTrading`（Python 3.10.14），不污染系统 Python；建议用该环境的解释器执行命令。依赖以 `pyproject.toml` 为准。

### 6.2 启动 Web 看板

```bash
python run_webapp.py
# 首页 http://localhost:8000  API 文档 http://localhost:8000/docs
```

`run_webapp.py` 用 uvicorn 以 `reload=True` 起 `webapp.main:app`。

### 6.3 运行研究流水线

```bash
python -m research.main \
    --start-date 2024-01-01 --end-date 2025-12-31 \
    --strategy eaa --top-n 5 --rebalance-freq 5d
```

输出到 `research/output/backtest_results/`（`summary.csv`、`factor_stats.csv`、`equity_curve.csv`、`weights.csv`、`synthesized_scores.csv`、`warnings.txt` + PNG 图）。

### 6.4 运行测试

```bash
pytest -v        # testpaths=tests，pythonpath=. （见 pyproject.toml）
pytest -q        # 快速静默
```

## 7. 测试约定

- 全量测试位于 `tests/`，文件名按 `test_<模块>.py` 组织，覆盖 core、research、webapp API、webapp e2e。
- `tests/conftest.py` 用 **临时 SQLite 库** 生成确定性假数据（schema 镜像真实库），核心测试通过 `SqliteDataSource` 读数据，不依赖真实行情。
- 新增因子应参照 `tests/test_factor_registry.py` 补注册测试；改调仓逻辑应补 `tests/test_calendar.py` / `tests/test_bt_engine.py`。

## 8. 代码风格与开发约定

- **Python ≥ 3.10**，文件普遍以 `from __future__ import annotations` 开头，类型标注用 `X | None` 语法。
- **注释/文档串多用中文**（README、docstring、yaml 注释、schema 字段说明均为中文）。
- 基础设施与实现分离：`base.py`（抽象基类）、`exceptions.py`（异常）、`utils.py`（工具）、`registry.py`（注册表）+ `__init__.py`（re-export）。
- 配置用 **dataclass / pydantic 模型**承载（`ResearchConfig` 为 frozen dataclass，`WebAppConfig` 由 pydantic 从 YAML + 环境变量加载）。
- YAML 配置是「唯一来源」：因子 (`factors.yaml` / `factor_config.yaml`)、web (`config/webapp.yaml`)。
- 生成物（`data/*.db`、`research/output/`、缓存、`__pycache__`）按 `.gitignore` 排除，不提交。
- 调参/网格搜索脚本可复用 `research/tune_strategy_params.py`。

## 9. 重要注意点 / 易踩坑

1. **因子列顺序**：`build()` 返回矩阵的列必须与 `universe` 完全一致，否则后续对齐出错。
2. **因子改名报错**：`Factor 'xxx' is not registered` 通常是 `factors.yaml`/`factor_config.yaml` 里 `name` 拼错，或研究池与 core 都没注册该因子。
3. **研究因子不影响 Web**：两池隔离是设计行为，移植按 4.3 节操作。
4. **IC 全 NaN**：截面标的太少（IC 至少 `ic_min_observations=10` 个有效观测）。
5. **价格对不上行情软件**：库里 `close` 是后复权价，与行情软件的未复权现价本就不同口径；系统不保存真实价（复权因子已删除），无法再换算真实现价。
6. **默认参数改一处漏一处**：改 FAA/EAA 默认权重/指数/β，需同时改 `webapp/services/strategy_service.py` 与 `research/config.py`。
7. **数据同步**：research 只读不回写；新增标的后需在 Web 同步行情，否则回测该标的数据缺失。
8. **回测起始期**：长窗口因子需要预热数据，Web 策略用 `_WARMUP_DAYS=250`，research 用 `--data-start-date` 提前加载（IC/回测仍从 `--start-date` 起算）。