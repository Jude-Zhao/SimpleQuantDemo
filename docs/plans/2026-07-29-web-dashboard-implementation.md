# Web 因子看板实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 SimpleQuantDemo 构建完整的 Web 因子看板，包括因子展示、三种策略运行、标的池管理、插件式因子库、分类约束体系五大能力。

**Architecture:** 模块化单体架构。core/ 层保持纯计算不变，新增 webapp/ 模块（FastAPI + services + SQLAlchemy + 静态前端），通过 services 层调用 core 能力。前端为纯静态 HTML + 原生 JS + ECharts，通过 REST API 与后端交互。

**Tech Stack:** FastAPI, Uvicorn, SQLAlchemy, SQLite, Pydantic, scipy, baostock, AkShare, ECharts

---

## 阶段总览

| 阶段 | 内容 | 任务数 |
|------|------|--------|
| 阶段 0 | 基础设施：依赖、配置、数据库、应用骨架 | 5 |
| 阶段 1 | 因子插件化改造 + 因子服务 + 因子 API | 5 |
| 阶段 2 | 数据源扩展：baostock + 缓存 + data_service | 4 |
| 阶段 3 | 标的池管理：模型 + 服务 + API | 4 |
| 阶段 4 | 分类约束体系：规则引擎 + 约束校验 + 优化器扩展 | 5 |
| 阶段 5 | MVO + Black-Litterman 优化器 | 4 |
| 阶段 6 | 策略运行服务 + API + 回测集成 | 4 |
| 阶段 7 | 前端：骨架 + 首页 + 因子看板 | 5 |
| 阶段 8 | 前端：策略运行 + 标的池 + 分类约束 + 设置 | 5 |
| 阶段 9 | 集成测试 + 文档 + 收尾 | 3 |

---

## 阶段 0：基础设施

### Task 0.1: 更新 pyproject.toml 依赖

**Files:**
- Modify: `pyproject.toml`

**Step 1: 读取当前 pyproject.toml**

Run: 读取 `pyproject.toml` 的 [project] 部分，了解现有依赖

**Step 2: 添加新依赖**

在 dependencies 中新增：
```
fastapi = ">=0.100.0"
uvicorn = {extras = ["standard"], version = ">=0.23.0"}
sqlalchemy = ">=2.0.0"
pydantic = ">=2.0.0"
pydantic-settings = ">=2.0.0"
pyyaml = ">=6.0"
scipy = ">=1.10.0"
baostock = ">=0.8.8"
python-dotenv = ">=1.0.0"
```

在 [project.optional-dependencies] 中保留 test 组（pytest）。

**Step 3: 安装依赖验证**

Run: `pip install -e ".[test]"`（在项目根目录）
Expected: 所有依赖安装成功

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add web dashboard dependencies"
```

---

### Task 0.2: 配置体系（YAML + Pydantic）

**Files:**
- Create: `config/webapp.yaml`
- Create: `webapp/__init__.py`
- Create: `webapp/config.py`
- Create: `.env.example`

**Step 1: 创建 webapp 包初始化文件**

`webapp/__init__.py` 内容为空即可。

**Step 2: 编写配置文件模板**

`config/webapp.yaml`:
```yaml
server:
  host: "0.0.0.0"
  port: 8000

database:
  url: "sqlite:///./data/simple_quant.db"

datasource:
  primary: "akshare"
  secondary: "baostock"
  cache_enabled: true
  cache_days: 365

factors:
  auto_discover: true
  scan_package: "core.factors"

strategy:
  max_history_runs: 100
```

**Step 3: 编写 Pydantic 配置类**

`webapp/config.py`:
```python
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/simple_quant.db"


class DatasourceConfig(BaseModel):
    primary: str = "akshare"
    secondary: str = "baostock"
    cache_enabled: bool = True
    cache_days: int = 365


class FactorsConfig(BaseModel):
    auto_discover: bool = True
    scan_package: str = "core.factors"


class StrategyConfig(BaseModel):
    max_history_runs: int = 100


class WebAppSettings(BaseSettings):
    """从环境变量覆盖的设置（敏感信息）"""
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}


class WebAppConfig(BaseModel):
    server: ServerConfig
    database: DatabaseConfig
    datasource: DatasourceConfig
    factors: FactorsConfig
    strategy: StrategyConfig


def _load_yaml_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_config() -> WebAppConfig:
    """加载配置：YAML 为主，环境变量覆盖敏感字段"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "webapp.yaml"
    yaml_data = _load_yaml_config(config_path)

    # 环境变量覆盖
    env_settings = WebAppSettings()
    if env_settings.database_url:
        yaml_data.setdefault("database", {})["url"] = env_settings.database_url

    return WebAppConfig(**yaml_data)
```

**Step 4: 编写 .env.example**

```
# 数据库连接（覆盖 webapp.yaml 中的 database.url）
# DATABASE_URL=sqlite:///./data/simple_quant.db
```

**Step 5: 写测试验证配置加载**

Create: `tests/test_webapp_config.py`
```python
from webapp.config import get_config, WebAppConfig

def test_config_loads():
    cfg = get_config()
    assert isinstance(cfg, WebAppConfig)
    assert cfg.server.port == 8000
    assert cfg.database.url.startswith("sqlite")
```

**Step 6: 运行测试**

Run: `pytest tests/test_webapp_config.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add webapp/__init__.py webapp/config.py config/webapp.yaml .env.example tests/test_webapp_config.py
git commit -m "feat: add webapp config system (YAML + Pydantic)"
```

---

### Task 0.3: 数据库连接与 ORM 基类

**Files:**
- Create: `webapp/models/__init__.py`
- Create: `webapp/models/database.py`

**Step 1: 编写数据库连接模块**

`webapp/models/database.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from webapp.config import get_config

config = get_config()

connect_args = {}
if config.database.url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    config.database.url,
    connect_args=connect_args,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


def get_db():
    """FastAPI 依赖：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表"""
    # 确保 data 目录存在
    from pathlib import Path
    if config.database.url.startswith("sqlite"):
        db_path = config.database.url.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
```

**Step 2: models/__init__.py 留空**

**Step 3: 写测试验证数据库连接**

Create: `tests/test_webapp_database.py`
```python
from webapp.models.database import engine, Base, SessionLocal, init_db

def test_database_engine():
    assert engine is not None

def test_session_factory():
    session = SessionLocal()
    assert session is not None
    session.close()

def test_init_db():
    init_db()
    # 不报错即为通过
```

**Step 4: 运行测试**

Run: `pytest tests/test_webapp_database.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/models/__init__.py webapp/models/database.py tests/test_webapp_database.py
git commit -m "feat: add database ORM base and connection"
```

---

### Task 0.4: FastAPI 应用骨架

**Files:**
- Create: `webapp/main.py`
- Create: `webapp/api/__init__.py`
- Create: `webapp/api/health.py`
- Create: `webapp/static/index.html`

**Step 1: 编写健康检查 API**

`webapp/api/health.py`:
```python
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", version="1.0.0")
```

**Step 2: 编写 api/__init__.py（留空）**

**Step 3: 编写 FastAPI 主应用**

`webapp/main.py`:
```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webapp.api.health import router as health_router
from webapp.config import get_config
from webapp.models.database import init_db


def create_app() -> FastAPI:
    config = get_config()

    # 初始化数据库
    init_db()

    app = FastAPI(title="SimpleQuant Web Dashboard", version="1.0.0")

    # API 路由
    app.include_router(health_router)

    # 静态文件
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def root():
        return {"message": "SimpleQuant Web Dashboard", "docs": "/docs"}

    return app


app = create_app()
```

**Step 4: 创建静态文件目录骨架和简单 index.html**

`webapp/static/index.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SimpleQuant 看板</title>
</head>
<body>
    <h1>SimpleQuant Web Dashboard</h1>
    <p>建设中...</p>
</body>
</html>
```

**Step 5: 写测试验证 API**

Create: `tests/test_webapp_api_health.py`
```python
from fastapi.testclient import TestClient
from webapp.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data

def test_root():
    response = client.get("/")
    assert response.status_code == 200
```

**Step 6: 运行测试**

Run: `pytest tests/test_webapp_api_health.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add webapp/main.py webapp/api/__init__.py webapp/api/health.py webapp/static/index.html tests/test_webapp_api_health.py
git commit -m "feat: add FastAPI app skeleton with health check"
```

---

### Task 0.5: schemas 目录与 services 目录骨架

**Files:**
- Create: `webapp/schemas/__init__.py`
- Create: `webapp/services/__init__.py`

**Step 1: 创建 schemas/__init__.py（留空）**

**Step 2: 创建 services/__init__.py（留空）**

**Step 3: Commit**

```bash
git add webapp/schemas/__init__.py webapp/services/__init__.py
git commit -m "chore: add schemas and services package skeletons"
```

---

## 阶段 1：因子插件化改造

### Task 1.1: 因子注册表 + 自动发现

**Files:**
- Create: `core/factors/registry.py`
- Modify: `core/factors/base.py`
- Modify: `core/factors/momentum.py`
- Modify: `core/factors/volatility.py`

**Step 1: 读取现有 base.py, momentum.py, volatility.py**

了解 FactorBuilder 接口和现有因子实现。

**Step 2: 编写注册表模块**

`core/factors/registry.py`:
```python
import importlib
import pkgutil
from typing import Callable, Optional

from core.factors.base import FactorBuilder

_factor_registry: dict[str, type[FactorBuilder]] = {}
_discovered = False


def register_factor(name: str | None = None) -> Callable[[type[FactorBuilder]], type[FactorBuilder]]:
    """装饰器：注册因子类到全局注册表"""
    def decorator(cls: type[FactorBuilder]) -> type[FactorBuilder]:
        factor_name = name or cls.__name__
        _factor_registry[factor_name] = cls
        return cls
    return decorator


def discover_factors(package_name: str = "core.factors") -> None:
    """自动扫描包内所有模块，触发装饰器注册"""
    global _discovered
    if _discovered:
        return

    package = importlib.import_module(package_name)
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        if module_name in ("base", "registry", "utils", "__init__"):
            continue
        importlib.import_module(f"{package_name}.{module_name}")

    _discovered = True


def get_factor_registry() -> dict[str, type[FactorBuilder]]:
    """获取因子注册表（确保已扫描）"""
    discover_factors()
    return dict(_factor_registry)


def get_factor_class(name: str) -> type[FactorBuilder] | None:
    """根据名称获取因子类"""
    registry = get_factor_registry()
    return registry.get(name)


def list_factor_names() -> list[str]:
    """列出所有已注册因子名称"""
    return list(get_factor_registry().keys())
```

**Step 3: 给 FactorBuilder 基类添加元数据属性**

在 `core/factors/base.py` 的 FactorBuilder 类中添加：
```python
@property
def name(self) -> str:
    """因子唯一标识名"""
    raise NotImplementedError

display_name: str = ""
description: str = ""
params_schema: dict = {}  # {param_name: {type, default, min, max, label}}
```

注意：保留原有的抽象方法，只新增类属性。

**Step 4: 改造 momentum.py 使用装饰器并添加元数据**

添加 `@register_factor("momentum")` 装饰器，添加 display_name, description, params_schema 类属性。

params_schema 示例：
```python
params_schema = {
    "window": {
        "type": "int",
        "default": 5,
        "min": 1,
        "max": 252,
        "label": "窗口天数"
    }
}
```

**Step 5: 改造 volatility.py 使用装饰器并添加元数据**

同样添加装饰器和 params_schema（window, annualization 两个参数）。

**Step 6: 写测试验证注册表**

Create: `tests/test_factor_registry.py`
```python
from core.factors.registry import get_factor_registry, get_factor_class, list_factor_names, discover_factors

def test_discover_factors():
    discover_factors()
    names = list_factor_names()
    assert "momentum" in names
    assert "volatility" in names

def test_get_factor_class():
    cls = get_factor_class("momentum")
    assert cls is not None
    # 能实例化
    factor = cls(window=10)
    assert factor.name == "momentum"

def test_factor_meta():
    cls = get_factor_class("momentum")
    assert hasattr(cls, "display_name")
    assert hasattr(cls, "params_schema")
    assert "window" in cls.params_schema
```

**Step 7: 运行测试**

Run: `pytest tests/test_factor_registry.py -v`
Expected: PASS（同时确保原有因子测试也通过）

Run: `pytest tests/test_factors.py -v`
Expected: PASS

**Step 8: Commit**

```bash
git add core/factors/registry.py core/factors/base.py core/factors/momentum.py core/factors/volatility.py tests/test_factor_registry.py
git commit -m "feat: add plugin-style factor registry with auto-discovery"
```

---

### Task 1.2: 新增示例因子（价值类）+ 验证插件机制

**Files:**
- Create: `core/factors/value.py`

**Step 1: 新增一个价值类因子作为插件示例**

`core/factors/value.py`：实现一个简单的"反转因子"（N日收益率取反，作为价值类因子的简化版），验证插件机制。

```python
import pandas as pd
from core.factors.base import FactorBuilder
from core.factors.registry import register_factor


@register_factor("reversal")
class ReversalFactor(FactorBuilder):
    name = "reversal"
    display_name = "反转因子"
    description = "N日收盘价收益率的相反数，用于捕捉短期反转效应"
    params_schema = {
        "window": {
            "type": "int",
            "default": 20,
            "min": 1,
            "max": 252,
            "label": "窗口天数"
        }
    }

    def __init__(self, window: int = 20):
        self.window = window

    def build(self, price_data: pd.DataFrame, macro_data: pd.DataFrame, universe: list[str]) -> pd.DataFrame:
        close = price_data["close"].pivot_table(index="date", columns="sec", values="close")
        close = close[universe]
        ret = close.pct_change(self.window)
        return -ret
```

**Step 2: 写测试验证新因子自动注册**

在 `tests/test_factor_registry.py` 中添加：
```python
def test_reversal_factor_auto_registered():
    names = list_factor_names()
    assert "reversal" in names

def test_reversal_factor_build():
    import pandas as pd
    cls = get_factor_class("reversal")
    factor = cls(window=5)
    # 构造简单数据
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    prices = pd.DataFrame({
        "date": dates.repeat(2),
        "sec": ["A", "B"] * 10,
        "close": [100, 50, 101, 51, 102, 52, 103, 53, 104, 54,
                  105, 55, 106, 56, 107, 57, 108, 58, 109, 59],
    })
    result = factor.build(prices, pd.DataFrame(), ["A", "B"])
    assert result.shape[1] == 2
```

**Step 3: 运行测试**

Run: `pytest tests/test_factor_registry.py tests/test_factors.py -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add core/factors/value.py tests/test_factor_registry.py
git commit -m "feat: add reversal factor as plugin example"
```

---

### Task 1.3: Factor Service + schemas

**Files:**
- Create: `webapp/schemas/factor.py`
- Create: `webapp/services/factor_service.py`

**Step 1: 编写 Pydantic schema**

`webapp/schemas/factor.py`:
```python
from pydantic import BaseModel, Field
from typing import Any


class FactorParamSchema(BaseModel):
    type: str
    default: Any
    min: float | int | None = None
    max: float | int | None = None
    label: str


class FactorMeta(BaseModel):
    name: str
    display_name: str
    description: str
    params_schema: dict[str, FactorParamSchema]


class FactorComputeRequest(BaseModel):
    factor_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None


class FactorICResult(BaseModel):
    ic_mean: float
    ic_std: float
    icir: float
    rank_ic_mean: float
    rank_ic_std: float
    rank_icir: float
    ic_series: dict[str, float]  # date -> ic value


class FactorComputeResponse(BaseModel):
    factor_name: str
    ic_result: FactorICResult
    # 分组收益后续再加
```

**Step 2: 编写 factor_service**

`webapp/services/factor_service.py`:
```python
import pandas as pd
from core.factors.registry import get_factor_class, get_factor_registry
from core.analysis.ic import calculate_ic_series, calculate_ic_summary
from webapp.schemas.factor import FactorMeta, FactorParamSchema, FactorComputeResponse, FactorICResult


def list_factors() -> list[FactorMeta]:
    """获取所有因子的元信息"""
    registry = get_factor_registry()
    result = []
    for name, cls in registry.items():
        params = {k: FactorParamSchema(**v) for k, v in cls.params_schema.items()}
        result.append(FactorMeta(
            name=name,
            display_name=getattr(cls, "display_name", name),
            description=getattr(cls, "description", ""),
            params_schema=params,
        ))
    return result


def compute_factor(
    factor_name: str,
    params: dict,
    price_data: pd.DataFrame,
    macro_data: pd.DataFrame,
    universe: list[str],
    horizon: int = 5,
) -> FactorComputeResponse:
    """计算因子并返回IC分析结果"""
    cls = get_factor_class(factor_name)
    if cls is None:
        raise ValueError(f"Factor not found: {factor_name}")

    factor = cls(**params)
    factor_matrix = factor.build(price_data, macro_data, universe)

    # 计算IC
    close = price_data.pivot_table(index="date", columns="sec", values="close")
    close = close[universe]
    future_ret = close.shift(-horizon) / close - 1

    ic_series_df = calculate_ic_series(factor_matrix, future_ret)
    ic_summary = calculate_ic_summary(ic_series_df)

    # 转换为响应格式
    ic_series_dict = {
        str(k): float(v) for k, v in ic_series_df["ic"].dropna().items()
    }

    return FactorComputeResponse(
        factor_name=factor_name,
        ic_result=FactorICResult(
            ic_mean=float(ic_summary["ic_mean"]),
            ic_std=float(ic_summary["ic_std"]),
            icir=float(ic_summary["icir"]),
            rank_ic_mean=float(ic_summary["rank_ic_mean"]),
            rank_ic_std=float(ic_summary["rank_ic_std"]),
            rank_icir=float(ic_summary["rank_icir"]),
            ic_series=ic_series_dict,
        ),
    )
```

注意：需要先读取 `core/analysis/ic.py` 确认函数签名和返回格式，再适配。

**Step 3: 写测试**

Create: `tests/test_factor_service.py`
```python
import pandas as pd
from webapp.services.factor_service import list_factors, compute_factor

def test_list_factors():
    factors = list_factors()
    assert len(factors) >= 3  # momentum, volatility, reversal
    names = [f.name for f in factors]
    assert "momentum" in names
    assert factors[0].params_schema is not None

def test_compute_factor_with_sample_data():
    # 构造测试数据
    dates = pd.date_range("2024-01-01", periods=30, freq="B")
    n_sec = 5
    secs = [f"ETF{i:02d}" for i in range(n_sec)]

    # 构造 price_data（long format）
    rows = []
    import numpy as np
    np.random.seed(42)
    for i, date in enumerate(dates):
        for j, sec in enumerate(secs):
            base_price = 100 + j * 10
            noise = np.random.randn() * 0.5
            rows.append({
                "date": date,
                "sec": sec,
                "close": base_price + i * 0.3 + noise,
            })
    price_data = pd.DataFrame(rows)

    result = compute_factor(
        factor_name="momentum",
        params={"window": 5},
        price_data=price_data,
        macro_data=pd.DataFrame(),
        universe=secs,
        horizon=5,
    )
    assert result.factor_name == "momentum"
    assert result.ic_result.ic_mean is not None
    assert isinstance(result.ic_result.ic_series, dict)
```

**Step 4: 运行测试**

Run: `pytest tests/test_factor_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/schemas/factor.py webapp/services/factor_service.py tests/test_factor_service.py
git commit -m "feat: add factor service with IC computation"
```

---

### Task 1.4: 因子 API 路由

**Files:**
- Create: `webapp/api/factors.py`
- Modify: `webapp/main.py`

**Step 1: 编写因子 API**

`webapp/api/factors.py`:
```python
from fastapi import APIRouter, HTTPException
from webapp.schemas.factor import FactorMeta, FactorComputeRequest, FactorComputeResponse
from webapp.services.factor_service import list_factors, compute_factor

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("", response_model=list[FactorMeta])
def get_factors():
    """获取所有可用因子的元信息"""
    return list_factors()


@router.post("/compute", response_model=FactorComputeResponse)
def compute_factor_endpoint(req: FactorComputeRequest):
    """计算指定因子并返回分析结果"""
    # TODO: 接入真实数据源，目前先用测试数据占位
    # 实际实现时从 data_service 获取数据
    raise HTTPException(status_code=501, detail="待接入数据源后实现")
```

**Step 2: 在 main.py 中注册路由**

在 `create_app()` 中添加：
```python
from webapp.api.factors import router as factors_router
app.include_router(factors_router)
```

**Step 3: 写测试验证 API**

Add to `tests/test_webapp_api_health.py` 或新建 `tests/test_webapp_api_factors.py`:
```python
from fastapi.testclient import TestClient
from webapp.main import app

client = TestClient(app)

def test_get_factors():
    response = client.get("/api/factors")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 3
    names = [f["name"] for f in data]
    assert "momentum" in names
    assert "volatility" in names
    assert "reversal" in names
    assert "params_schema" in data[0]
```

**Step 4: 运行测试**

Run: `pytest tests/test_webapp_api_factors.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/api/factors.py webapp/main.py tests/test_webapp_api_factors.py
git commit -m "feat: add factors API endpoint"
```

---

### Task 1.5: 因子分组收益计算

**Files:**
- Modify: `webapp/services/factor_service.py`
- Modify: `webapp/schemas/factor.py`

**Step 1: 在 schemas 中添加分组收益结构**

```python
class FactorGroupReturn(BaseModel):
    group: int  # 1-5, 1为最低分组，5为最高分组
    annual_return: float
    cumulative_return: float


class FactorComputeResponse(BaseModel):
    factor_name: str
    ic_result: FactorICResult
    group_returns: list[FactorGroupReturn]
```

**Step 2: 在 factor_service 中实现分组收益计算**

实现逻辑：
1. 每期按因子值将标的分为 N 组（默认5组）
2. 计算每组等权收益序列
3. 汇总年化收益和累计收益

```python
def _calculate_group_returns(
    factor_matrix: pd.DataFrame,
    price_data: pd.DataFrame,
    universe: list[str],
    n_groups: int = 5,
    horizon: int = 5,
) -> list[FactorGroupReturn]:
    """计算因子分组收益"""
    close = price_data.pivot_table(index="date", columns="sec", values="close")
    close = close[universe]
    future_ret = close.shift(-horizon) / close - 1

    group_returns = {i: [] for i in range(1, n_groups + 1)}
    group_dates = []

    for date in factor_matrix.index:
        if date not in future_ret.index:
            continue
        fac = factor_matrix.loc[date].dropna()
        ret = future_ret.loc[date].dropna()
        common = fac.index.intersection(ret.index)
        if len(common) < n_groups:
            continue
        fac = fac[common]
        ret = ret[common]

        # 按因子值排序分组
        ranked = fac.rank(pct=True)
        for i in range(n_groups):
            lower = i / n_groups
            upper = (i + 1) / n_groups
            mask = (ranked > lower) & (ranked <= upper)
            if mask.sum() > 0:
                group_returns[i + 1].append(ret[mask].mean())
        group_dates.append(date)

    result = []
    for g in range(1, n_groups + 1):
        if len(group_returns[g]) == 0:
            result.append(FactorGroupReturn(group=g, annual_return=0.0, cumulative_return=0.0))
            continue
        rets = pd.Series(group_returns[g])
        cum = (1 + rets).prod() - 1
        # 年化（假设 horizon 个交易日，一年252天）
        n_periods = len(rets)
        if n_periods > 0:
            annual = (1 + cum) ** (252 / (horizon * n_periods)) - 1 if cum > -1 else -1.0
        else:
            annual = 0.0
        result.append(FactorGroupReturn(group=g, annual_return=float(annual), cumulative_return=float(cum)))

    return result
```

在 `compute_factor` 函数中调用并加入返回值。

**Step 3: 写测试验证分组收益**

在 `tests/test_factor_service.py` 中添加测试。

**Step 4: 运行测试**

Run: `pytest tests/test_factor_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/services/factor_service.py webapp/schemas/factor.py tests/test_factor_service.py
git commit -m "feat: add factor group return calculation"
```

---

## 阶段 2：数据源扩展

### Task 2.1: baostock 数据源

**Files:**
- Create: `core/data/baostock_source.py`
- Modify: `core/data/base.py`（如有需要）

**Step 1: 读取现有 DataSource 基类和 csv_source.py**

了解接口定义。

**Step 2: 实现 BaostockDataSource**

`core/data/baostock_source.py`:
```python
import pandas as pd
from core.data.base import DataSource


class BaostockDataSource(DataSource):
    """baostock 数据源"""

    def __init__(self):
        self._lg = None

    def _login(self):
        if self._lg is None:
            import baostock as bs
            self._lg = bs.login()

    def get_etf_price(self, sec_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """获取ETF日线行情"""
        self._login()
        import baostock as bs

        all_data = []
        for sec_code in sec_codes:
            # baostock 代码格式：sh.510300 / sz.159915
            bs_code = self._convert_code(sec_code)
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,code,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="1",  # 后复权
            )
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())
            if data_list:
                df = pd.DataFrame(data_list, columns=rs.fields)
                df["sec"] = sec_code
                all_data.append(df)

        if not all_data:
            return pd.DataFrame(columns=["date", "sec", "open", "high", "low", "close", "volume", "amount"])

        result = pd.concat(all_data, ignore_index=True)
        # 重命名和类型转换
        result = result.rename(columns={"code": "bs_code"})
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            result[col] = pd.to_numeric(result[col], errors="coerce")
        result["date"] = pd.to_datetime(result["date"])
        return result[["date", "sec", "open", "high", "low", "close", "volume", "amount"]]

    def _convert_code(self, sec_code: str) -> str:
        """转换代码格式：510300.SH -> sh.510300"""
        code, market = sec_code.split(".")
        return f"{market.lower()}.{code}"

    # 其他接口先返回空，按需实现
    def get_macro_data(self, indicators: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def get_etf_list(self) -> pd.DataFrame:
        return pd.DataFrame()
```

**Step 3: 写测试**

Create: `tests/test_baostock_source.py`
```python
import pytest

def test_baostock_code_conversion():
    from core.data.baostock_source import BaostockDataSource
    ds = BaostockDataSource()
    assert ds._convert_code("510300.SH") == "sh.510300"
    assert ds._convert_code("159915.SZ") == "sz.159915"

@pytest.mark.skipif(True, reason="需要网络连接，手动测试")
def test_baostock_get_price():
    from core.data.baostock_source import BaostockDataSource
    ds = BaostockDataSource()
    df = ds.get_etf_price(["510300.SH"], "2024-01-01", "2024-01-31")
    assert not df.empty
    assert "close" in df.columns
```

**Step 4: 运行测试**

Run: `pytest tests/test_baostock_source.py -v`
Expected: PASS（跳过需要网络的测试）

**Step 5: Commit**

```bash
git add core/data/baostock_source.py tests/test_baostock_source.py
git commit -m "feat: add baostock data source adapter"
```

---

### Task 2.2: 行情缓存表 + CachedDataSource

**Files:**
- Modify: `webapp/models/database.py`（添加行情表模型）
- Create: `core/data/cached_source.py`

**Step 1: 在 models 中添加行情缓存表**

新建 `webapp/models/market_data.py`：
```python
from sqlalchemy import Column, String, Date, Float, Index
from webapp.models.database import Base


class EtfDailyBar(Base):
    """ETF日线行情缓存表"""
    __tablename__ = "etf_daily_bar"

    id = Column(String, primary_key=True)  # sec_code + trade_date
    sec_code = Column(String, nullable=False)
    trade_date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    source = Column(String)  # akshare / baostock

    __table_args__ = (
        Index("ix_etf_bar_sec_date", "sec_code", "trade_date", unique=True),
    )
```

在 `webapp/models/__init__.py` 中导入模型以确保注册。

**Step 2: 实现 CachedDataSource**

`core/data/cached_source.py` 作为包装器，包装底层数据源，优先读缓存。

由于 core 层不依赖 webapp 的 ORM，这里采用"依赖注入"方式：传入读写函数。

```python
import pandas as pd
from core.data.base import DataSource


class CachedDataSource(DataSource):
    """带缓存的数据源包装器"""

    def __init__(
        self,
        primary_source: DataSource,
        secondary_source: DataSource | None = None,
        cache_reader=None,  # callable(sec_codes, start, end) -> DataFrame
        cache_writer=None,  # callable(df) -> None
    ):
        self.primary = primary_source
        self.secondary = secondary_source
        self.cache_reader = cache_reader
        self.cache_writer = cache_writer

    def get_etf_price(self, sec_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        # 1. 尝试读缓存
        if self.cache_reader:
            cached = self.cache_reader(sec_codes, start_date, end_date)
            if cached is not None and not cached.empty:
                # 检查是否覆盖全部请求范围
                cached_codes = set(cached["sec"].unique())
                missing_codes = [c for c in sec_codes if c not in cached_codes]
                if not missing_codes:
                    return cached
                # 部分缺失，继续拉取
                fetch_codes = missing_codes
            else:
                fetch_codes = list(sec_codes)
        else:
            fetch_codes = list(sec_codes)
            cached = pd.DataFrame()

        # 2. 从主数据源拉取
        fresh_df = pd.DataFrame()
        try:
            fresh_df = self.primary.get_etf_price(fetch_codes, start_date, end_date)
            fresh_df["source"] = "akshare"
        except Exception:
            # 3. 主源失败，尝试备用源
            if self.secondary:
                fresh_df = self.secondary.get_etf_price(fetch_codes, start_date, end_date)
                fresh_df["source"] = "baostock"

        # 4. 写入缓存
        if self.cache_writer and not fresh_df.empty:
            self.cache_writer(fresh_df)

        # 5. 合并缓存 + 新数据
        if cached.empty:
            return fresh_df
        if fresh_df.empty:
            return cached
        return pd.concat([cached, fresh_df], ignore_index=True).drop_duplicates(
            subset=["sec", "date"], keep="last"
        )

    def get_macro_data(self, indicators: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        return self.primary.get_macro_data(indicators, start_date, end_date)

    def get_etf_list(self) -> pd.DataFrame:
        return self.primary.get_etf_list()
```

**Step 3: 写测试**

用 mock 的 cache_reader/cache_writer 测试缓存逻辑。

**Step 4: 运行测试**

Run: `pytest tests/test_cached_source.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/models/market_data.py core/data/cached_source.py tests/test_cached_source.py
git commit -m "feat: add cached data source with SQLite cache"
```

---

### Task 2.3: data_service 服务层

**Files:**
- Create: `webapp/services/data_service.py`

**Step 1: 实现 data_service**

封装数据获取逻辑，对接缓存 + 底层数据源。提供给其他 service 调用。

```python
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from core.data.akshare_source import AkShareDataSource
from core.data.baostock_source import BaostockDataSource
from core.data.cached_source import CachedDataSource
from webapp.config import get_config
from webapp.models.market_data import EtfDailyBar


_config = get_config()
_primary = AkShareDataSource()
_secondary = BaostockDataSource()


def _cache_reader(db: Session):
    """创建缓存读取函数"""
    def reader(sec_codes, start_date, end_date):
        start = pd.to_datetime(start_date).date()
        end = pd.to_datetime(end_date).date()
        rows = db.query(EtfDailyBar).filter(
            EtfDailyBar.sec_code.in_(sec_codes),
            EtfDailyBar.trade_date >= start,
            EtfDailyBar.trade_date <= end,
        ).all()
        if not rows:
            return pd.DataFrame()
        data = [{
            "date": r.trade_date,
            "sec": r.sec_code,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "volume": r.volume,
            "amount": r.amount,
        } for r in rows]
        return pd.DataFrame(data)
    return reader


def _cache_writer(db: Session):
    """创建缓存写入函数"""
    def writer(df: pd.DataFrame):
        for _, row in df.iterrows():
            sec = row["sec"]
            date_val = pd.to_datetime(row["date"]).date()
            bar_id = f"{sec}_{date_val.isoformat()}"
            existing = db.query(EtfDailyBar).filter_by(id=bar_id).first()
            if existing:
                continue
            bar = EtfDailyBar(
                id=bar_id,
                sec_code=sec,
                trade_date=date_val,
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
                source=row.get("source", ""),
            )
            db.add(bar)
        db.commit()
    return writer


def get_cached_source(db: Session) -> CachedDataSource:
    """获取带缓存的数据源"""
    return CachedDataSource(
        primary_source=_primary,
        secondary_source=_secondary,
        cache_reader=_cache_reader(db) if _config.datasource.cache_enabled else None,
        cache_writer=_cache_writer(db) if _config.datasource.cache_enabled else None,
    )


def get_etf_price(db: Session, sec_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    """获取ETF行情数据"""
    source = get_cached_source(db)
    return source.get_etf_price(sec_codes, start_date, end_date)


def get_etf_list(db: Session) -> list[dict]:
    """获取可交易ETF列表"""
    # 优先从 AkShare 获取，失败返回内置列表
    try:
        df = _primary.get_etf_list()
        if not df.empty:
            return df.to_dict("records")
    except Exception:
        pass
    return _get_default_etf_list()


def _get_default_etf_list() -> list[dict]:
    """内置默认ETF列表"""
    return [
        {"sec_code": "510300.SH", "sec_name": "沪深300ETF", "type": "宽基"},
        {"sec_code": "510500.SH", "sec_name": "中证500ETF", "type": "宽基"},
        {"sec_code": "159915.SZ", "sec_name": "创业板ETF", "type": "宽基"},
        {"sec_code": "518880.SH", "sec_name": "黄金ETF", "type": "商品"},
        {"sec_code": "511010.SH", "sec_name": "国债ETF", "type": "债券"},
    ]
```

**Step 2: 写测试**

测试 data_service 的基本功能（用内存 SQLite）。

**Step 3: 运行测试**

Run: `pytest tests/test_data_service.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add webapp/services/data_service.py tests/test_data_service.py
git commit -m "feat: add data service with cached ETF price access"
```

---

### Task 2.4: 完善因子 compute API（接入真实数据）

**Files:**
- Modify: `webapp/api/factors.py`
- Modify: `webapp/services/factor_service.py`

**Step 1: 改造 factor_service 接受数据源参数**

让 compute_factor 函数从 data_service 获取数据，而不是调用方传入。

或者保持纯函数风格，由 API 层负责获取数据后传入。

采用方案：API 层获取数据 → 传入 factor_service（保持 service 纯计算）。

**Step 2: 完善 factors API 的 compute 端点**

从数据库获取标的池 → 获取行情 → 调用 factor_service → 返回结果。

标的池先从默认列表取，等阶段3完成后再替换为数据库标的池。

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git commit -m "feat: connect factor compute API with real data source"
```

---

## 阶段 3：标的池管理

### Task 3.1: 标的池 ORM 模型

**Files:**
- Create: `webapp/models/universe.py`
- Modify: `webapp/models/__init__.py`

**Step 1: 实现 UniverseItem 模型**

```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON
from webapp.models.database import Base


class UniverseItem(Base):
    """标的池"""
    __tablename__ = "universe_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sec_code = Column(String, unique=True, nullable=False, index=True)
    sec_name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, index=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    removed_at = Column(DateTime, nullable=True)
    meta = Column(JSON, default=dict)  # 扩展字段
```

在 `webapp/models/__init__.py` 中导入以注册。

**Step 2: 重新初始化数据库表验证**

**Step 3: Commit**

```bash
git add webapp/models/universe.py webapp/models/__init__.py
git commit -m "feat: add universe item ORM model"
```

---

### Task 3.2: 标的池 schemas + service

**Files:**
- Create: `webapp/schemas/universe.py`
- Create: `webapp/services/universe_service.py`

**Step 1: 编写 Pydantic schemas**

```python
from pydantic import BaseModel
from datetime import datetime
from typing import Any


class UniverseItemBase(BaseModel):
    sec_code: str
    sec_name: str
    meta: dict[str, Any] = {}


class UniverseItemCreate(UniverseItemBase):
    pass


class UniverseItemResponse(UniverseItemBase):
    id: int
    is_active: bool
    added_at: datetime
    removed_at: datetime | None = None

    class Config:
        from_attributes = True
```

**Step 2: 编写 universe_service**

CRUD 操作：list_active, add, remove, get_available。

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/schemas/universe.py webapp/services/universe_service.py tests/test_universe_service.py
git commit -m "feat: add universe service with CRUD operations"
```

---

### Task 3.3: 标的池 API 路由

**Files:**
- Create: `webapp/api/universe.py`
- Modify: `webapp/main.py`

**Step 1: 实现 API**

- GET /api/universe — 当前标的池
- POST /api/universe — 批量添加
- DELETE /api/universe/{sec_code} — 移除
- GET /api/universe/available — 可添加 ETF 列表

**Step 2: 注册路由**

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/api/universe.py webapp/main.py tests/test_webapp_api_universe.py
git commit -m "feat: add universe management API"
```

---

### Task 3.4: 初始化默认标的池

**Files:**
- Modify: `webapp/models/database.py` 或新建初始化脚本

**Step 1: 在 init_db 中添加默认标的池初始化**

如果表为空，插入默认的 5 只 ETF。

**Step 2: 测试验证**

**Step 3: Commit**

```bash
git commit -m "feat: seed default universe items on init"
```

---

## 阶段 4：分类约束体系

### Task 4.1: 分类规则 ORM 模型

**Files:**
- Create: `webapp/models/classification.py`

**Step 1: 实现 ClassificationRule 模型**

字段：id, rule_name, category_key, rule_type, config(JSON), is_active, priority

**Step 2: 注册模型**

**Step 3: Commit**

```bash
git add webapp/models/classification.py
git commit -m "feat: add classification rule ORM model"
```

---

### Task 4.2: 分类规则 schemas + service

**Files:**
- Create: `webapp/schemas/classification.py`
- Create: `webapp/services/classification_service.py`

**Step 1: 编写 schemas**

规则的 CRUD schema + 分类结果 schema。

**Step 2: 实现 classification_service**

- 规则 CRUD
- classify_universe()：按优先级执行所有 active 规则，返回分类结果
- 支持三种规则类型：manual, by_field, by_range

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/schemas/classification.py webapp/services/classification_service.py tests/test_classification_service.py
git commit -m "feat: add classification rule engine service"
```

---

### Task 4.3: 分类约束 API

**Files:**
- Create: `webapp/api/classifications.py`
- Modify: `webapp/main.py`

**Step 1: 实现 API**

- GET /api/classifications/rules
- POST /api/classifications/rules
- PUT /api/classifications/rules/{id}
- DELETE /api/classifications/rules/{id}
- POST /api/classifications/apply
- GET /api/constraints
- PUT /api/constraints

**Step 2: 注册路由**

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/api/classifications.py webapp/main.py tests/test_webapp_api_classification.py
git commit -m "feat: add classification and constraints API"
```

---

### Task 4.4: 约束校验模块（core 层）

**Files:**
- Create: `core/optimization/constraints.py`

**Step 1: 定义约束数据结构**

用 dataclass 定义 CategoryConstraint 和 OptimizationConstraints（保持 core 层不依赖 Pydantic）。

**Step 2: 实现 validate_constraints 函数**

校验权重是否满足所有约束，返回违规项列表。

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add core/optimization/constraints.py tests/test_constraints.py
git commit -m "feat: add portfolio constraint validation module"
```

---

### Task 4.5: 等权优化器扩展分类约束

**Files:**
- Modify: `core/optimization/equal_weight.py`

**Step 1: 扩展 EqualWeightOptimizer 支持分类权重约束**

在选标的和分配权重时考虑分类约束。

**Step 2: 写测试**

**Step 3: 运行测试**

**Step 4: Commit**

```bash
git add core/optimization/equal_weight.py tests/test_optimization.py
git commit -m "feat: extend equal-weight optimizer with category constraints"
```

---

## 阶段 5：MVO + Black-Litterman 优化器

### Task 5.1: MVO 均值方差优化器

**Files:**
- Create: `core/optimization/mvo.py`

**Step 1: 实现 MVOptimizer**

继承 PortfolioOptimizer 基类，用 scipy.optimize.minimize 实现。

支持：
- 目标：最小方差 / 最大夏普 / 目标收益
- 约束：权重和为1、单票上下限、分类权重上下限
- 输入：预期收益、协方差矩阵、约束配置

**Step 2: 写测试**

**Step 3: 运行测试**

**Step 4: Commit**

```bash
git add core/optimization/mvo.py tests/test_mvo.py
git commit -m "feat: add mean-variance portfolio optimizer"
```

---

### Task 5.2: Black-Litterman 模型

**Files:**
- Create: `core/optimization/bl.py`

**Step 1: 实现 Black-Litterman 收益估计**

实现 BL 后验收益和后验协方差计算：
- 先验收益 Π（市场均衡收益，由市值权重反推）
- 观点矩阵 P、观点收益 Q、观点置信度 Ω
- 后验收益 E(R) = (Σ⁻¹ + P'Ω⁻¹P)⁻¹ · (Σ⁻¹Π + P'Ω⁻¹Q)
- 后验协方差 M = Σ + [Σ⁻¹ + P'Ω⁻¹P]⁻¹

**Step 2: 实现 BLOptimizer**

BL 收益估计 + MVO 优化器求解。

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add core/optimization/bl.py tests/test_bl.py
git commit -m "feat: add Black-Litterman optimizer"
```

---

### Task 5.3: 优化器工厂 + 统一接口

**Files:**
- Create: `core/optimization/factory.py`
- Modify: `core/optimization/__init__.py`（如有）

**Step 1: 实现优化器工厂**

根据策略类型返回对应优化器实例。

**Step 2: 写测试**

**Step 3: Commit**

```bash
git add core/optimization/factory.py
git commit -m "feat: add optimizer factory for unified interface"
```

---

### Task 5.4: 约束集成到 MVO 和 BL 优化器

**Files:**
- Modify: `core/optimization/mvo.py`
- Modify: `core/optimization/bl.py`

**Step 1: 将分类约束作为 scipy 优化的不等式约束**

**Step 2: 优化后调用 validate_constraints 做兜底校验**

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add core/optimization/mvo.py core/optimization/bl.py
git commit -m "feat: integrate category constraints into MVO and BL optimizers"
```

---

## 阶段 6：策略运行服务

### Task 6.1: 策略运行记录 ORM 模型

**Files:**
- Create: `webapp/models/strategy_run.py`

**Step 1: 实现 StrategyRun 模型**

字段：id, strategy_type, params(JSON), universe_snapshot(JSON), start_date, end_date, result_summary(JSON), status, error_msg, created_at

**Step 2: 注册模型**

**Step 3: Commit**

```bash
git add webapp/models/strategy_run.py
git commit -m "feat: add strategy run ORM model"
```

---

### Task 6.2: 策略 schemas + strategy_service

**Files:**
- Create: `webapp/schemas/strategy.py`
- Create: `webapp/services/strategy_service.py`

**Step 1: 编写 schemas**

- 策略元信息（名称、描述、参数 schema）
- 运行请求
- 运行结果响应（净值、绩效、权重、约束校验结果）
- 运行记录列表项

**Step 2: 实现 strategy_service**

- list_strategies()：返回三种策略的元信息
- run_strategy(db, strategy_type, params)：核心编排函数
  - 读取标的池
  - 获取行情数据
  - 按策略类型计算：
    - linear_factor：因子计算 → 合成 → 等权优化 → 回测
    - mvo：收益估计 → 协方差 → MVO → 回测
    - bl：先验 + 观点 → BL → 优化 → 回测
  - 约束校验
  - 保存运行记录
  - 返回结果

**Step 3: 复用回测引擎**

直接调用 research/backtest.py 中的回测函数，或提取到 core 层。

建议：将回测核心逻辑提取到 core/ 下，让 research/ 和 webapp/ 都能调用。

**Step 4: 写测试**

**Step 5: 运行测试**

**Step 6: Commit**

```bash
git add webapp/schemas/strategy.py webapp/services/strategy_service.py tests/test_strategy_service.py
git commit -m "feat: add strategy service with three strategy types"
```

---

### Task 6.3: 策略 API 路由

**Files:**
- Create: `webapp/api/strategies.py`
- Modify: `webapp/main.py`

**Step 1: 实现 API**

- GET /api/strategies — 策略列表 + 参数 schema
- POST /api/strategies/run — 运行策略
- GET /api/strategies/runs — 历史记录
- GET /api/strategies/runs/{id} — 运行详情
- GET /api/strategies/runs/{id}/export — 导出 CSV

**Step 2: 注册路由**

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/api/strategies.py webapp/main.py tests/test_webapp_api_strategies.py
git commit -m "feat: add strategy execution API"
```

---

### Task 6.4: 回测引擎提取到 core 层

**Files:**
- Create: `core/backtest/engine.py`
- Modify: `research/backtest.py`（改为调用 core 层）

**Step 1: 将回测核心逻辑提取到 core/backtest/**

让 research 和 webapp 共用同一套回测逻辑。

**Step 2: 改造 research/backtest.py 调用 core 层**

**Step 3: 确保所有现有测试通过**

**Step 4: Commit**

```bash
git add core/backtest/engine.py research/backtest.py
git commit -m "refactor: extract backtest engine to core layer"
```

---

## 阶段 7：前端 - 骨架 + 首页 + 因子看板

### Task 7.1: 前端骨架：侧边栏 + 路由 + 基础样式

**Files:**
- Modify: `webapp/static/index.html`
- Create: `webapp/static/css/app.css`
- Create: `webapp/static/js/app.js`
- Create: `webapp/static/js/api.js`

**Step 1: 重写 index.html**

完整的单页应用骨架：
- 左侧边栏导航（首页、因子看板、策略运行、标的池、分类约束、设置）
- 右侧主内容区
- 引入 ECharts CDN
- 引入 app.js, api.js

**Step 2: 编写基础 CSS**

侧边栏样式、内容区布局、卡片样式、表格样式、表单样式。

**Step 3: 编写简易 Hash 路由**

根据 URL hash 切换页面内容。

**Step 4: 编写 api.js 封装**

封装 fetch 调用，统一处理错误。

**Step 5: 手动验证**

启动服务，访问首页看布局是否正确。

**Step 6: Commit**

```bash
git add webapp/static/index.html webapp/static/css/app.css webapp/static/js/app.js webapp/static/js/api.js
git commit -m "feat: add frontend skeleton with sidebar navigation"
```

---

### Task 7.2: 首页 Dashboard

**Files:**
- Create: `webapp/static/js/dashboard.js`

**Step 1: 实现首页内容**

- 4 个统计卡片（标的池数量、因子数量、今日运行次数、系统状态）
- ETF 走势折线图（ECharts，多线，下拉选择标的，默认5只）
- 因子 IC 排名条形图
- 最近策略运行记录列表
- 净值曲线对比图（最近3次运行）

**Step 2: 添加首页 API（如需要）**

- GET /api/dashboard/summary — 首页汇总数据

**Step 3: 手动验证**

**Step 4: Commit**

```bash
git add webapp/static/js/dashboard.js
git commit -m "feat: add dashboard home page with ETF charts"
```

---

### Task 7.3: 因子看板页面

**Files:**
- Create: `webapp/static/js/factors.js`

**Step 1: 实现因子看板**

- 顶部筛选栏：因子多选、时间范围、调仓频率、刷新按钮
- 因子指标概览卡片（IC、RankIC、ICIR、年化收益）
- IC / RankIC / ICIR 时序图（ECharts 多线对比）
- 分组收益柱状图
- 因子相关性热力图

**Step 2: 手动验证**

**Step 3: Commit**

```bash
git add webapp/static/js/factors.js
git commit -m "feat: add factor analysis dashboard page"
```

---

### Task 7.4: 首页 API 端点

**Files:**
- Create: `webapp/api/dashboard.py`
- Modify: `webapp/main.py`

**Step 1: 实现首页汇总 API**

- GET /api/dashboard/stats — 统计卡片数据
- GET /api/dashboard/etf-price?codes=xxx — ETF 走势数据
- GET /api/dashboard/factor-ranking — 因子 IC 排名
- GET /api/dashboard/recent-runs — 最近运行记录

**Step 2: 注册路由**

**Step 3: 写测试**

**Step 4: 运行测试**

**Step 5: Commit**

```bash
git add webapp/api/dashboard.py webapp/main.py tests/test_webapp_api_dashboard.py
git commit -m "feat: add dashboard summary API endpoints"
```

---

### Task 7.5: 因子相关性分析（后端）

**Files:**
- Modify: `webapp/services/factor_service.py`
- Modify: `webapp/schemas/factor.py`
- Modify: `webapp/api/factors.py`

**Step 1: 添加多因子相关性计算**

计算因子间相关系数矩阵，供前端热力图使用。

**Step 2: 添加 API 端点**

POST /api/factors/correlation

**Step 3: 测试**

**Step 4: Commit**

```bash
git commit -m "feat: add factor correlation analysis endpoint"
```

---

## 阶段 8：前端 - 策略运行 + 标的池 + 分类约束 + 设置

### Task 8.1: 策略运行页面

**Files:**
- Create: `webapp/static/js/strategies.js`

**Step 1: 实现策略页面**

- 策略选择器（线性因子/MVO/BL）
- 参数表单（根据策略类型动态渲染）
  - 线性因子：因子多选 + 权重滑块、Top N、调仓频率、约束配置
  - MVO：目标收益/风险厌恶、协方差窗口、约束配置
  - BL：观点编辑表格、约束配置
- "运行策略"按钮
- 结果展示区：净值曲线图、绩效卡片、权重条形图、约束校验结果
- 历史运行记录列表

**Step 2: 手动验证**

**Step 3: Commit**

```bash
git add webapp/static/js/strategies.js
git commit -m "feat: add strategy execution page"
```

---

### Task 8.2: 标的池管理页面

**Files:**
- Create: `webapp/static/js/universe.js`

**Step 1: 实现标的池页面**

- 当前标的列表表格
- 添加标的（从可选列表中选择）
- 移除标的
- 搜索、筛选
- 分类标签显示和编辑

**Step 2: 手动验证**

**Step 3: Commit**

```bash
git add webapp/static/js/universe.js
git commit -m "feat: add universe management page"
```

---

### Task 8.3: 分类约束页面

**Files:**
- Create: `webapp/static/js/classification.js`

**Step 1: 实现分类约束页面**

- 分类规则列表
- 新增/编辑规则表单（根据规则类型动态显示字段）
- 删除规则
- "应用分类"按钮 + 分类结果预览表格
- 约束配置表单（按类别设置权重上下限）

**Step 2: 手动验证**

**Step 3: Commit**

```bash
git add webapp/static/js/classification.js
git commit -m "feat: add classification and constraints page"
```

---

### Task 8.4: 设置页面

**Files:**
- Create: `webapp/static/js/settings.js`

**Step 1: 实现设置页面**

- 数据源配置（主/备源选择、缓存开关）
- 数据刷新按钮
- 系统信息（版本、数据库状态）

**Step 2: 添加设置 API**

**Step 3: 手动验证**

**Step 4: Commit**

```bash
git add webapp/static/js/settings.js
git commit -m "feat: add settings page"
```

---

### Task 8.5: 前端样式优化 + 响应式

**Files:**
- Modify: `webapp/static/css/app.css`

**Step 1: 优化样式**

- 统一配色和间距
- 卡片阴影和圆角
- 表格样式优化
- 表单样式优化
- 移动端适配（可选，第一版可只做桌面端）

**Step 2: 手动验证各页面**

**Step 3: Commit**

```bash
git add webapp/static/css/app.css
git commit -m "feat: polish frontend styles and responsive layout"
```

---

## 阶段 9：集成测试 + 文档 + 收尾

### Task 9.1: 端到端集成测试

**Files:**
- Create: `tests/test_webapp_e2e.py`

**Step 1: 编写端到端测试**

用 TestClient 测试完整流程：
1. 添加标的到标的池
2. 获取因子列表
3. 计算因子
4. 运行线性因子策略
5. 查看运行记录
6. 添加分类规则
7. 应用分类

**Step 2: 运行测试**

Run: `pytest tests/test_webapp_e2e.py -v`
Expected: PASS

**Step 3: 运行全部测试确保无回归**

Run: `pytest -v`
Expected: ALL PASS

**Step 4: Commit**

```bash
git add tests/test_webapp_e2e.py
git commit -m "test: add end-to-end integration tests for webapp"
```

---

### Task 9.2: 用户文档 + README 更新

**Files:**
- Create: `docs/Web看板使用手册.md`
- Modify: `README.md`（如果有）

**Step 1: 编写 Web 看板使用手册**

- 启动方式
- 各页面功能说明
- 常见问题

**Step 2: 更新项目 README**

添加 Web 看板相关说明。

**Step 3: Commit**

```bash
git add docs/Web看板使用手册.md
git commit -m "docs: add web dashboard user manual"
```

---

### Task 9.3: 启动脚本 + 收尾

**Files:**
- Create: `run_webapp.py` 或添加启动脚本

**Step 1: 添加启动脚本**

方便用户一键启动。

```python
# run_webapp.py
import uvicorn

if __name__ == "__main__":
    uvicorn.run("webapp.main:app", host="0.0.0.0", port=8000, reload=True)
```

**Step 2: 最终验证**

- 启动服务
- 访问各页面
- 跑通完整流程

**Step 3: Commit**

```bash
git add run_webapp.py
git commit -m "feat: add webapp startup script"
```

---

**计划完成。总计约 45 个任务，分 9 个阶段。**
