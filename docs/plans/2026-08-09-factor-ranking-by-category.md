# 首页因子看板按分类分组展示设计

日期：2026-08-09
状态：已实现（前端采用单个 ECharts 分块图）
范围：改造首页「因子 RankIC 排名」卡片，使其遵循 `factors.yaml` 的分类体系，同时展示每个因子实例的得分与每个分类的类得分

## 1. 背景与现状

首页底部「因子 RankIC 排名」卡片（`webapp/api/dashboard.py` 的
`/api/dashboard/factor-ranking` + `webapp/static/js/pages/dashboard.js` 的
`loadFactorRanking`）当前机制：

- 后端遍历 `list_factors()`（**注册表里的因子类**：momentum / volatility /
  reversal，各取一个**默认窗口**），逐个计算 RankIC 均值 / IR；
- 返回 `FactorRankingItem`：`name / display_name / rank_ic_mean / rank_icir`，
  **不含分类信息**；
- 前端按 RankIC 均值降序，渲染成**单一**横向条形图。

**问题**：策略运行用的是 `factors.yaml` 的 5 类（动量/波动/反转/量能/其他），
每类下是**因子实例（带窗口参数）**（如动量类含 window=20/60/120 三个实例）。
而首页因子看板展示的是注册因子类（3 类、各一个默认窗口），两者**不是同一套
体系**，首页无法反映策略实际使用的分类结构。

## 2. 目标

首页「因子 RankIC 排名」按 `factors.yaml` 分类分组展示，每类同时给出：

1. **单个因子实例的得分**：该类内每个因子实例（含窗口参数）的 RankIC 均值/IR；
2. **类得分**：该类内因子等权合成的「类得分」矩阵的 RankIC 均值/IR。

## 3. 复用现有纯函数

- `core/factors/config.py`：`list_factor_categories()` — 返回分类（key、
  display_name、factors[name, params]），含空类；
- `webapp/services/eaa_faa.py`：
  - `build_category_factors(price_data, universe, category)` — 该类下每个实例的因子矩阵；
  - `build_category_scores(price_data, universe, categories)` — {cat_key: 类得分矩阵}；
- `core/analysis/ic.py`：`calculate_forward_returns(...)`、`calculate_rank_ic(...)`、
  `calculate_icir(...)`。

## 4. 后端改动（webapp/api/dashboard.py）

`get_factor_ranking` 重写为按分类组织：

1. `categories = list_factor_categories()`（含空类 volume / other）；
2. `forward_returns = calculate_forward_returns(price_data, horizon=5, universe)`；
3. 对每个分类：
   - **空类**（volume / other）：仅返回分类头，`is_empty=true`，无因子实例、无类得分；
   - **非空类**：
     - 因子实例：`build_category_factors(...)` 得到每个实例矩阵 → `calculate_rank_ic`
       取均值 / `calculate_icir` 取 IR；
     - 类得分：`build_category_scores(...)[cat.key]` → 同样算 RankIC 均值 / IR；
4. 返回结构（新 schema）：

```json
[
  {
    "key": "momentum",
    "display_name": "动量",
    "is_empty": false,
    "class_rank_ic_mean": 0.03,
    "class_rank_icir": 0.4,
    "factors": [
      { "name": "momentum", "params": {"window": 20},
        "rank_ic_mean": 0.02, "rank_icir": 0.3 },
      ...更多实例
    ]
  },
  { "key": "volume", "display_name": "量能", "is_empty": true,
    "class_rank_ic_mean": null, "factors": [] }
]
```

- 缓存 key（universe, 最新 bar 日期）与加锁逻辑保持不变。
- 单因子计算路径改为直接复用 `build_category_factors`（与 `compute_factor` 用的
  是同一 `build` 与 `calculate_rank_ic`，结果一致），不再依赖 `list_factors()`。

### 4.1 Schema 调整

- `FactorRankingItem`：新增 `key / display_name / is_empty / class_rank_ic_mean /
  class_rank_icir / factors`，移除旧的顶层 `name / display_name / rank_ic_mean /
  rank_icir`；
- 新增 `FactorInstanceRanking`：`name / params / rank_ic_mean / rank_icir`。

## 5. 前端改动（webapp/static/js/pages/dashboard.js）

`loadFactorRanking` 从「单一降序条形图」改为「按分类分组」。

- 布局：卡片从 `col-4` 扩为独立整行（`col-12`），容纳 5 类；
- 每个分类一个小节：
  - 标题：分类名 +「类得分 RankIC」徽标（含均值/IR，空类显示「暂无因子」）；
  - 组内：各因子实例横向条形（名称标注窗口，如「动量(20)」），按 RankIC 均值排序；
  - 类得分条用与因子条不同的强调色以区分「类」与「单因子」层级；
- 空类（量能/其他）仅显示分类头 + 空态。

### 5.1 渲染形式（待用户确认）

- 方案 A：**单个 ECharts 图**，yAxis 分多块（每类一块，类得分加粗 + 区块分隔），
  与其余图表视觉一致；
- 方案 B：**HTML/CSS 条形**（每类一个 `<div>` 小节，紧凑列表），布局更自由、
  更易塞进页面，但与该页其他 ECharts 图表风格略异。

## 6. 测试

- 更新 `tests/test_webapp_api_strategies.py` 或新增 dashboard 相关测试：
  - 返回结构含 5 类、空类标记正确；
  - 非空类的 `class_rank_ic_mean` 为有限数值，`factors` 数量与 YAML 配置一致；
  - 空类 `factors` 为空、`class_rank_ic_mean` 为 null。
- 端到端：`pytest` 全量回归。

## 7. 影响范围

- 仅改 `webapp/api/dashboard.py`（后端 + schema）与 `webapp/static/js/pages/dashboard.js`
  （前端渲染）两处；
- 不触碰策略运行、分类配置、因子库核心逻辑；
- `compute_factor`（因子看板页）保持不变，仅首页因子 IC 排名卡片改用分类展示。