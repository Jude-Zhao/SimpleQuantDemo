# EAA / FAA 策略替换设计

日期：2026-08-09
状态：待确认
范围：移除线性因子 / 均值方差（MVO）/ Black-Litterman 三个策略，替换为 EAA、FAA 两个动量配置策略

## 1. 背景与目标

当前 webapp 提供三个策略：`linear_factor`（线性因子）、`mvo`（均值方差优化）、`bl`（Black-Litterman）。
用户不再需要优化类策略，改为两个**自定义变体**的动量打分策略：

- **FAA（Flexible Asset Allocation）**：因子类得分的线性加权组合
- **EAA（Enhanced Asset Allocation）**：因子类得分的幂函数乘法组合

两者共用同一套「因子分类 → 类内等权合成 → 类间加权 → Top N 选标的」流水线，区别仅在综合得分公式与持仓方式。

## 2. 需求确认（对话决策）

| 决策点 | 结论 |
|---|---|
| GAA 动态增长 | **不做**，无攻守调节、无安全资产强制配置 |
| FAA 公式 | 简单多项式加权（rank 线性组合），选 Top N 后**组内等权** |
| EAA 公式 | 因子得分排名 → 各自乘方缩放系数 α → 相乘 → 整体乘方 β → 标准化得分；选 Top N 后**按得分占比加权** |
| 因子分类 | 5 类：动量 / 波动 / 反转 / 量能 / 其他（其他类预留宏观因子，当前为空） |
| 类内合成 | 类内因子**等权** |
| 类间权重 | 用户**页面可配置**（FAA 的 w 与 EAA 的 α、β 同为可配参数） |
| 因子方向 | 因子代码层直接处理方向（负向因子输出取反），策略层统一按「越大越好」rank |
| Top N | 3 / 5 / 7 / 9 可选 |
| 删除范围 | 连核心代码一起删（mvo.py / bl.py / factory.py 及对应测试） |

## 3. 策略定义

### 3.1 得分归一化定义（两策略共用）

对每个因子类 k，在某调仓日对池内全部标的状态的截面得分做 **min-max 归一化到 (ε, 1]**：

```
norm(类得分ₖ,ᵢ) = (xₖᵢ − minₖ) / (maxₖ − minₖ) × (1 − ε) + ε
```

- xₖᵢ：标的 i 在类 k 上的原始得分；minₖ / maxₖ：该日该类全部标的得分的最小/最大值
- ε 默认 0.01：避免 0 值（EAA 中 0^α 吞掉整个乘积）
- 若 maxₖ = minₖ（该类当日无区分度），全部标的一律取 1.0

**为什么不用序数排名**：排名是阶跃变换，丢失得分间的相对距离（如 0.99 与 0.01 的差距与 0.51 与 0.49 的差距都算"1 名"），且非线性变换会扭曲 EAA 的幂乘组合。
**为什么不用 z-score**：z-score 会产生负值，负数非整数次幂（rank^α）为 NaN，无法用于 EAA。
min-max 是保距的正线性变换，值域正、保留间距，两策略通用。

### 3.2 FAA（组内等权）

综合得分：

```
Lᵢ = Σₖ wₖ × norm(类得分ₖ,ᵢ)
```

- wₖ：用户配置的类间权重，默认按**非空类**等权（当前 3 个非空类各 1/3），要求 Σwₖ = 1（前端归一化 + 后端校验）
- 按 Lᵢ 降序选 Top N
- **持仓**：入选的 N 个标的等权，权重 = 1/N

### 3.3 EAA（按得分加权）

综合得分：

```
Sᵢ = ( Πₖ norm(类得分ₖ,ᵢ)^αₖ )^β
```

- αₖ：用户配置的类缩放系数（指数），默认 1.0，建议范围 0.1–5.0
- β：用户配置的整体缩放系数，默认 1.0，建议范围 0.1–5.0
- 按 Sᵢ 降序选 Top N
- **持仓**：权重 = Sᵢ / Σⱼ Sⱼ（入选集合内归一化）

### 3.4 持仓约束校验

EAA/FAA 均**不内设单票权重上限**，但回测结束后的最新持仓会走一轮
`validate_constraints` 约束校验，校验结果（violations）随运行结果返回并在
web「约束检查」卡片展示（复用现有 `_validate_portfolio` 与前端面板）。

## 4. 因子分类设计

### 4.1 分类总览

| 类 key | 中文名 | 当前因子 | 说明 |
|---|---|---|---|
| momentum | 动量 | momentum（已有） | 趋势强度 |
| volatility | 波动 | volatility（已有） | 低波动偏好，因子层输出取反 |
| reversal | 反转 | reversal（已有） | 短期均值回归（本身即负动量） |
| volume | 量能 | （空） | 待定，后续补充成交量类因子 |
| other | 其他 | （空） | 预留宏观因子 |

分类由配置文件驱动（见 4.3），后续新增因子只需在配置中登记，无需改策略代码。

### 4.2 默认分类配置

```
动量类   → momentum_20, momentum_60, momentum_120
波动类   → volatility_20, volatility_60（因子输出取反）
反转类   → reversal_5, reversal_20
量能类   → （空）
其他类   → （空）
```

窗口参数在配置文件中声明，后续可调；类别可增减。

### 4.3 因子分类配置文件

新增 `core/factors/builtin/factors.yaml`（沿用项目 YAML 配置惯例，pyyaml 已有依赖）：

```yaml
categories:
  momentum:
    display_name: "动量"
    factors:
      - name: momentum
        params: { window: 20 }
      - name: momentum
        params: { window: 60 }
      - name: momentum
        params: { window: 120 }
  volatility:
    display_name: "波动"
    factors:
      - name: volatility
        params: { window: 20 }
      - name: volatility
        params: { window: 60 }
  reversal:
    display_name: "反转"
    factors:
      - name: reversal
        params: { window: 5 }
      - name: reversal
        params: { window: 20 }
  volume:
    display_name: "量能"
    factors: []
  other:
    display_name: "其他"
    factors: []
```

加载器 `core/factors/config.py`：读取 YAML → 校验因子名存在于注册表 → 返回分类结构
（类 key、显示名、因子实例列表）。非空类才参与合成；空类在配置与 UI 中正常展示，权重自动视为 0 并对非空类重新归一化。

### 4.4 类得分合成

每日截面：
1. 每个因子实例输出原始因子矩阵（因子层已统一方向：越大越好）
2. 因子矩阵逐日做截面 min-max 归一化（同 3.1）→ 因子得分矩阵
3. 类内因子得分**等权平均**（axis=1）→ 类得分矩阵
4. 缺失处理：类内某因子某日全 NaN 则该因子当日跳过；类得分当日全 NaN 则该类当日不参与综合得分（min-max 前先 dropna 该日截面）

## 5. 参数配置（页面可配项）

### 5.1 两策略共用参数

| 参数 | 类型 | 默认 | 选项/范围 |
|---|---|---|---|
| top_n | int | 5 | 3 / 5 / 7 / 9 |
| rebalance_freq | str | monthly | weekly / monthly |
| class_weights | dict | 非空类等权 | 每类一个滑块，自动归一化 |
| factor_windows | json | 见 4.2 | 各因子窗口配置（沿用 multi_factor 交互风格） |

### 5.2 EAA 独有参数

| 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|
| exponents（αₖ） | dict | 各 1.0 | 0.1 – 5.0，每类一个 |
| beta（β） | float | 1.0 | 0.1 – 5.0 |

### 5.3 参数校验

- Σwₖ = 1（前端归一化 + 后端校验；空类权重视为 0，对非空类重新归一化）
- top_n ≤ 池内可算标的数（不足时报错提示）
- 「其他」「量能」类正常展示可配，但为空类时不参与合成，其权重/α 不生效

## 6. 技术改动清单

### 6.1 新增文件

| 文件 | 内容 |
|---|---|
| `core/factors/builtin/factors.yaml` | 因子分类配置文件（类 → 因子实例列表，见 4.3） |
| `core/factors/config.py` | YAML 分类配置加载器：读取 → 校验因子名 → 返回分类结构；提供 `list_factor_categories()` 供 API 与前端使用 |
| `core/optimization/score_weight.py` | `ScoreWeightedOptimizer`：按得分占比加权选 Top N（供 EAA 与回测引擎使用） |
| `webapp/services/eaa_faa_service.py`（或并入 strategy_service） | EAA/FAA 流水线：因子构建 → 类合成 → 综合得分 → 选标的 → 持仓 → 回测 → 约束校验 |
| `tests/test_eaa_faa.py` | min-max 归一化、类合成、FAA/EAA 公式、Top N、加权边界（0/NaN/全 NaN/ε） |

### 6.2 修改文件

| 文件 | 改动 |
|---|---|
| `core/factors/builtin/volatility.py` | 因子层直接输出取反值（direction 语义内化，与用户要求一致） |
| `core/backtest/engine.py` | `BacktestConfig` 增加 `weight_mode: "equal" / "score"`；`run_backtest` 按 mode 选择 EqualWeightOptimizer / ScoreWeightedOptimizer |
| `webapp/services/strategy_service.py` | `_STRATEGY_METAS` 替换为 eaa/faa；删除 `_run_linear_factor/_run_mvo/_run_bl`；新增 `_run_eaa/_run_faa`；分发逻辑更新；`_validate_portfolio` 复用 |
| `webapp/schemas/strategy.py` | 若需支持 dict 类型参数（class_weights / exponents），扩展 `StrategyParamSchema` 类型 |
| `webapp/static/js/pages/strategies.js` | 若 schema 渲染不覆盖「类权重滑块组」，增加对应控件 |
| `README.md`、`docs/Web看板使用手册.md` | 策略列表与说明更新 |
| `core/optimization/__init__.py` | 移除 bl/mvo/factory 导出，新增 score_weight |

### 6.3 删除文件

| 文件 | 原因 |
|---|---|
| `core/optimization/mvo.py` | MVO 不再使用 |
| `core/optimization/bl.py` | BL 不再使用 |
| `core/optimization/factory.py` | 仅服务于 mvo/bl/equal_weight 工厂化创建，EqualWeightOptimizer 由引擎直接实例化 |
| `tests/test_mvo.py`、`tests/test_bl.py`、`tests/test_optimizer_factory.py` | 对应删除 |

保留：`core/optimization/base.py`、`constraints.py`、`exceptions.py`、`equal_weight.py`
（`equal_weight.py` 被 `core/backtest/engine.py` 与 `trading/signal.py` 复用；constraints 用于约束校验 UI）

### 6.4 测试更新

- `tests/test_webapp_api_strategies.py`：断言策略列表改为 eaa/faa
- `tests/test_webapp_e2e.py`：`strategy_type` 从 linear_factor 改为 eaa（用最小可跑参数）
- `tests/test_optimization.py`、`tests/test_constraints.py`：保留（等权与约束逻辑不变）
- 新增 `tests/test_eaa_faa.py`

## 7. 数据与历史记录

- `strategy_runs` 表中旧策略类型（linear_factor / mvo / bl）的历史记录**清理删除**
  （SQL：`DELETE FROM strategy_runs WHERE strategy_type IN ('linear_factor','mvo','bl')`）
- 无需数据库迁移

## 8. 验证方案

1. 单元测试：`pytest tests/test_eaa_faa.py`（归一化、合成、公式、边界）
2. API 测试：策略列表含 eaa/faa；运行 eaa/faa 各一次（真实库 29 只 ETF），断言 metrics 非空、weights 总和=1、Top N 数量正确、约束校验结果返回
3. 前端：`node --check`；浏览器验证参数表单、约束检查卡片、运行结果渲染
4. 回归：`pytest` 全量（删除文件后的剩余测试必须全绿）

## 9. 待确认项

1. min-max 归一化到 (ε, 1]，ε=0.01（3.1）——是否接受
2. 因子分类默认配置（4.2：动量 3 窗口、波动 2 窗口、反转 2 窗口）——是否调整
3. 持仓不设单票上限，仅做约束校验展示（3.4）——确认
4. 空类（量能/其他）正常展示、不参与合成（4.3 / 5.3）——确认
5. 历史 run 记录清理删除（第 7 章）——确认
