# 因子数据源统一为 factors.yaml（去除 registry 作为前端列表来源）

日期：2026-08-09
状态：设计中
范围：统一全站（因子看板 / 首页 / 策略参数）的因子来源为 `factors.yaml`，使其与策略实际使用的分类体系一致

## 1. 背景与问题

当前全站因子列表存在**两套来源**，导致页面展示不一致：

- **首页「因子 RankIC 排名」**：读 `factors.yaml`（`list_factor_categories()`），展示 5 类（动量/波动/反转/量能/其他），每类下为**带窗口参数的因子实例**（如 momentum(20)/(60)/(120)）。
- **因子看板页「因子列表」**：读 **registry 注册表**（`list_factors()` → `/api/factors`），展示 3 个注册因子类（momentum/reversal/volatility），每类只有元信息、无窗口实例；且 `reversal.category="价值"`、`volatility.category="波动率"` 与 factors.yaml 的「反转」「波动」命名不一致。

策略运行（EAA/FAA）本身用的是 factors.yaml 分类。因此**因子看板页的列表与策略实际使用的因子不一致**，且分类标签命名也有出入。

**结论**：web 服务里因子应统一来源为 `factors.yaml`，registry 仅作为**因子实现 / 计算引擎**（build 方法、元信息），不再作为前端因子列表的组织来源。

## 2. 目标

1. 因子看板页「因子列表」改为按 `factors.yaml` 的 5 类分组展示**因子实例**（含窗口参数），与首页完全同构。
2. get /compute /correlation 三个接口的因子标识统一为「实例」（name + params）。
3. 相关性支持两种粒度：**因子实例间** 与 **因子类间**。
4. 首页 factor_count 统计与列表粒度一致。
5. 因子目录按分类重组，与 yaml 分类体系对齐。
6. 相关测试同步更新。

## 2.1 因子目录重组（与分类对齐）

当前 `core/factors/builtin/` 平铺堆放 3 个因子模块（momentum.py / volatility.py / value.py，其中 value.py 里藏着 reversal）。改为**每类一个子包文件夹**，与 `factors.yaml` 的 5 类一一对应：

```
core/factors/builtin/
├── __init__.py
├── factors.yaml              # 统一注册（5 类）
├── momentum/                 # 动量类
│   ├── __init__.py
│   └── momentum.py
├── volatility/               # 波动类
│   ├── __init__.py
│   └── volatility.py
├── reversal/                 # 反转类
│   ├── __init__.py
│   └── reversal.py           # 从 value.py 拆分出来
├── volume/                   # 量能类（当前空，预留）
│   └── __init__.py
└── other/                    # 其他类（当前空，预留）
    └── __init__.py
```

原则：
- 新增因子 = 放进对应分类的子包文件夹 + 在 `factors.yaml` 登记即可。
- 空类（volume / other）先建占位子包（仅 `__init__.py`），目录结构完整、未来直接往里加因子。
- registry 自动发现是**递归扫描** `core.factors` 包，子文件夹（子包）天然被覆盖，无需改扫描逻辑。
- 拆分：`ReversalFactor` 从 `value.py` 迁到 `reversal/reversal.py`，删除 `value.py`；同步更新 `core/factors/__init__.py` 的 re-export 与 README 目录说明。

## 3. 关键设计

### 3.1 因子实例的标识

一个实例由 `(name, params)` 唯一确定。前端计算/相关性时传递 `name` + `params`（不再只传 `name`）。

引入一个稳定的**实例标识**便于前端勾选与图表图例：
- 实例 id：`f"{name}({window})"` 形式（仅当 params 含 window 时），否则用 `name`。
- 与 `FactorBuilder.name` 属性（`momentum_20`）保持一致，但展示层用 `momentum(20)` 更直观，与首页一致。

### 3.2 后端接口

#### `/api/factors`（GET）—— 列表改为分类分组结构

复用 `factors.yaml`，返回结构与首页对齐，但**补全元信息**（display_name / description / formula / direction / params_schema 来自 registry 的因子类，供详情展示）。

新响应结构（`list[FactorCategoryMeta]`）：

```json
[
  {
    "key": "momentum",
    "display_name": "动量",
    "is_empty": false,
    "factors": [
      {
        "id": "momentum(20)",
        "name": "momentum",
        "params": {"window": 20},
        "display_name": "动量因子",
        "description": "...",
        "formula": "MOM(t) = close(t) / close(t-N) - 1",
        "direction": "positive",
        "params_schema": {...}
      },
      { "id": "momentum(60)", ... },
      { "id": "momentum(120)", ... }
    ]
  },
  { "key": "volume", "display_name": "量能", "is_empty": true, "factors": [] }
]
```

- `list_factors()` 改为基于 `list_factor_categories()` 展开，删除直接遍历 registry 的组织逻辑。
- 保留 `FactorMeta` 作为单个实例的 schema；新增 `FactorCategoryMeta`（key / display_name / is_empty / factors）。

#### `/api/factors/compute`（POST）—— 传实例

`FactorComputeRequest` 增加 `factor_id`（可选，兼容旧 `factor_name`），后端用 `name + params` 构建因子。前端传 `{ factor_id: "momentum(20)" }` 或 `{ factor_name: "momentum", params: {window:20} }`。

#### `/api/factors/correlation`（POST）—— 双粒度

请求改为接收实例列表，并支持两种粒度：

```json
{
  "granularity": "instance" | "class",
  "factor_ids": ["momentum(20)", "momentum(60)", "volatility(20)"],   // instance 粒度
  "factor_names": ["momentum", "volatility", "reversal"]               // class 粒度（每类取默认或合成类得分）
}
```

- **instance 粒度**：对每个实例 `(name, params)` 构建因子矩阵，计算两两截面相关性（复用现有 `compute_factor_correlation` 逻辑）。
- **class 粒度**：用 `build_category_scores()` 得到各类得分矩阵，对非空类得分矩阵计算截面相关性（类间相关性）。

### 3.3 前端（factors.js）

- `loadFactorList()`：改为渲染分类分组结构。每个类一个分组标题（类名），组下列出各实例 checkbox（`data-factor-id`）。空类显示「暂无因子」。
- 选中项现在为**实例 id**；`selectedFactorNames()` 改为基础实例 id 列表。
- `runSelectedFactors()`：对每个选中实例调 `/api/factors/compute`，传 `factor_id`。
- 相关性 tab：
  - 增加粒度切换（实例 / 类）。
  - 实例粒度：图表 x/y 轴为各实例标签。
  - 类粒度：图表 x/y 轴为非空类名。
- 详情卡片：实例点击后展示该实例所属类的元信息（formula / direction / params）。

### 3.4 首页 factor_count

`get_stats()` 中 `factor_count` 由 `len(list_factors())`（3 个类）改为**实例总数**（`sum(len(c.factors) for c in categories)`，当前 = 7）。

## 4. 影响文件

| 文件 | 改动 |
|---|---|
| `core/factors/builtin/momentum/` | 新建子包，迁入 momentum.py |
| `core/factors/builtin/volatility/` | 新建子包，迁入 volatility.py |
| `core/factors/builtin/reversal/` | 新建子包，迁入 reversal.py（由 value.py 拆分） |
| `core/factors/builtin/volume/`、`other/` | 新建空占位子包（仅 __init__.py） |
| `core/factors/builtin/value.py` | 删除（moving reversal） |
| `core/factors/__init__.py` | 更新 re-export 导入路径 |
| `core/factors/README.md` | 更新目录结构说明与内置因子清单 |
| `webapp/schemas/factor.py` | 新增 `FactorCategoryMeta`；`FactorMeta` 增加 `id` 字段 |
| `webapp/schemas/factor_correlation.py` | 请求增加 `granularity` / `factor_ids`；响应增加类粒度结构 |
| `webapp/services/factor_service.py` | 重写 `list_factors()` 基于 factors.yaml；`compute_factor` 支持 factor_id；`compute_factor_correlation` 支持双粒度 |
| `webapp/api/factors.py` | `/api/factors` 返回分类结构；compute / correlation 传参更新 |
| `webapp/api/dashboard.py` | `get_stats` 的 factor_count 改为实例数 |
| `webapp/static/js/api.js` | `listFactors` / `computeFactor` / `factorCorrelation` 传参与响应适配 |
| `webapp/static/js/pages/factors.js` | 列表按类分组、选中实例、相关性双粒度切换 |
| `tests/test_webapp_api_factors.py` | 断言改为分类结构 + 实例 |
| `tests/test_webapp_api_factor_correlation.py` | 双粒度测试 |
| `tests/test_webapp_api_dashboard.py` | factor_count 断言更新 |
| `tests/test_factor_service.py` | `list_factors` 结构断言更新 |
| `tests/test_webapp_e2e.py` | compute / factors 断言更新 |

## 5. 明确不做

- 不删除 registry / `get_factor_class`：它仍是因子实现与元信息来源，仅不再作为前端列表的组织来源。
- 不改变 EAA/FAA 策略运行逻辑（策略已用 factors.yaml）。
- 不改变首页「因子 RankIC 排名」已有行为。

## 6. 测试

- 列表接口：返回 5 类、空类标记、实例含 id/name/params、元信息完整。
- compute：传 `factor_id` 可计算对应实例。
- correlation：instance 粒度矩阵维度 = 实例数；class 粒度矩阵维度 = 非空类数；对角线为 1。
- 首页 factor_count 与实例总数一致。
- 全量 `pytest` 回归。