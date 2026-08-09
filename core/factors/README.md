# 因子系统用户手册

本手册介绍 SimpleQuantDemo 的**插件式因子库**：如何理解因子的目录结构、因子如何被自动发现、以及如何新增一个自定义因子。

## 一、概述

因子系统采用**插件式架构**：新增一个因子 = 在 `core/factors` 下新增一个 `.py` 文件并注册，**无需改动核心引擎**。系统启动时会自动扫描并加载所有因子，Web 看板会动态列出因子、渲染参数表单并展示因子表现。

## 二、目录结构

```
core/factors/
├── __init__.py          # 包入口，重新导出内置因子类
├── base.py              # FactorBuilder 抽象基类（因子必须继承它）
├── registry.py          # 因子注册表 + 自动发现机制
├── utils.py             # 公共工具函数（pivot / validate）
├── exceptions.py        # 因子校验异常
├── README.md            # 本手册
└── builtin/             # 内置因子目录（按分类分子包）
    ├── __init__.py
    ├── factors.yaml     # 因子分类注册（5 类，统一在此登记实例）
    ├── momentum/        # 动量类
    │   ├── __init__.py
    │   └── momentum.py
    ├── volatility/      # 波动类
    │   ├── __init__.py
    │   └── volatility.py
    ├── reversal/        # 反转类
    │   ├── __init__.py
    │   └── reversal.py
    ├── volume/          # 量能类（当前空，预留）
    │   └── __init__.py
    └── other/           # 其他类（当前空，预留）
        └── __init__.py
```

- **基础设施**（`base` / `registry` / `utils` / `exceptions`）与**因子实现**（`builtin/`）分离，扫描时自动跳过基础设施模块。
- 内置因子按**分类**放在 `builtin/` 下的子包文件夹中，与 `factors.yaml` 的 5 类一一对应。
- 新增因子流程：① 在对应分类的子包下新建 `.py` 文件并 `@register_factor` 注册；② 在 `factors.yaml` 对应分类下登记实例（name + params）。registry 递归扫描会自动发现子包中的因子。

## 三、自动发现机制

`core/factors/registry.py` 的 `discover_factors()` 在首次调用时**递归扫描** `core.factors` 包及其所有子包下的 `.py` 模块，导入模块时会执行 `@register_factor` 装饰器，从而把因子类登记进全局注册表。

- 扫描会**跳过**：`base`、`registry`、`utils`、`exceptions`、`__init__` 以及任意以 `_` 开头的私有模块。
- 注册表常用接口：
  - `list_factor_names()` → 返回所有已注册因子名列表
  - `get_factor_class(name)` → 按注册名取因子类
  - `get_factor_registry()` → 返回整个注册表字典

## 四、如何新增一个因子

### 步骤 1：新建文件

在 `core/factors/builtin/` 下**对应分类的子包**中新建 `.py` 文件，例如动量类下新建 `builtin/momentum/trend.py`。

> ⚠️ 文件名不能是 `base`、`registry`、`utils`、`exceptions`、`__init__`，也不能以 `_` 开头，否则不会被扫描。

### 步骤 2：编写因子类

继承 `FactorBuilder` 基类，用 `@register_factor("注册名")` 装饰，实现 `build()` 方法和 `name` 属性。参考 `core/factors/builtin/momentum.py` 的写法：

```python
"""示例：均线偏离度因子"""

from __future__ import annotations

import pandas as pd

from core.factors.base import FactorBuilder
from core.factors.registry import register_factor
from core.factors.utils import pivot_price_field, validate_factor_matrix


@register_factor("ma_deviation")
class MADeviationFactor(FactorBuilder):
    """价格相对均线的偏离度因子。"""

    registry_name = "ma_deviation"
    display_name = "均线偏离度因子"
    category = "趋势"
    description = "收盘价相对N日均线的偏离百分比"
    formula = "MAD(t) = close(t) / MA(close, N)(t) - 1"
    direction = "negative"          # 偏离过大倾向于均值回归
    params_schema = {               # 驱动前端参数表单
        "window": {
            "type": "int",
            "default": 20,
            "min": 2,
            "max": 120,
            "step": 1,
            "label": "均线窗口",
        }
    }

    def __init__(self, window: int = 20) -> None:
        if window <= 1:
            raise ValueError("window must be > 1.")
        self.window = window

    @property
    def name(self) -> str:
        return f"ma_deviation_{self.window}"

    def build(
        self,
        price_data: pd.DataFrame,
        macro_data: pd.DataFrame,
        universe: list[str],
    ) -> pd.DataFrame:
        close = pivot_price_field(price_data, field="close", universe=universe)
        ma = close.rolling(self.window).mean()
        factor = close / ma - 1
        factor.index.name = "date"
        validate_factor_matrix(factor, universe, name=self.name)
        return factor
```

### 步骤 3：在 factors.yaml 登记

`factors.yaml` 是因子列表的唯一来源（Web 看板 / 首页 / 策略共用）。在对应分类下登记你想用的**实例**（name + 参数）：

```yaml
categories:
  momentum:
    display_name: "动量"
    factors:
      - name: ma_deviation
        params: { window: 20 }
```

每个分类可登记多个实例（不同窗口）。未在 yaml 登记的因子即使已注册，也不会出现在 Web 看板。

### 步骤 4：验证接入

1. 启动服务或调用 `core.factors.registry.discover_factors()`，新因子即被自动注册。
2. 用 `get_factor_class("ma_deviation")` 取回并构建，确认可正常计算。
3. 参考 `tests/test_factor_registry.py` 补充一个注册测试。

## 五、接口约定

### 类级元数据（供前端展示）

| 属性 | 必填 | 说明 |
|------|------|------|
| `registry_name` | 是 | 注册名，通常与 `@register_factor` 一致 |
| `display_name` | 建议 | 前端显示的中文名 |
| `category` | 建议 | 分类，如"动量 / 价值 / 波动率 / 趋势" |
| `description` | 建议 | 因子逻辑的一段说明 |
| `formula` | 建议 | 纯文本公式（前端展示） |
| `direction` | 建议 | `"positive"`（越高越看多）或 `"negative"`（越低越看多） |
| `params_schema` | 建议 | 参数字典，驱动 Web 参数表单 |

`params_schema` 中每个参数的字段：`type`（int/float）、`default`、`min`、`max`、`step`、`label`。`__init__` 的形参要与之一一对应。

### 实例方法

| 成员 | 必填 | 说明 |
|------|------|------|
| `__init__(*args)` | 按需 | 构造参数须与 `params_schema` 对应 |
| `name` 属性 | 是 | 唯一因子名（含参数），用于面板和日志 |
| `build(price_data, macro_data, universe)` | 是 | 核心计算逻辑 |

### `build()` 输入输出约定

- **输入**
  - `price_data`: 长表 DataFrame，含 `date / sec / open / high / low / close / volume / amount` 列
  - `macro_data`: 宏观数据 DataFrame
  - `universe`: 标的代码列表
- **输出**: `pd.DataFrame`，**索引为日期 `DatetimeIndex`，列为标的代码**，且列顺序必须与 `universe` 完全一致
- **推荐**用 `utils.pivot_price_field()` 把长表转成行列矩阵，用 `utils.validate_factor_matrix()` 校验输出格式

## 六、内置因子清单

| 注册名 | 显示名 | 分类 | 方向 | 公式 |
|--------|--------|------|------|------|
| `momentum` | 动量因子 | 动量 | positive | MOM(t) = close(t) / close(t-N) - 1 |
| `reversal` | 反转因子 | 反转 | positive | REV(t) = -(close(t) / close(t-N) - 1) |
| `volatility` | 波动率因子 | 波动 | positive | VOL(t) = -std(returns(t-N+1..t)) * sqrt(annualization) |

> 以上为**注册名**；实际在 Web 看板 / 首页展示的是 `factors.yaml` 中登记的**实例**（如 `momentum(20)` / `momentum(60)`），每个实例带独立窗口参数。