from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ServerConfig(BaseModel):
    # 默认仅监听本机；显式改为 0.0.0.0 可对外绑定，但外部绑定不自动提供认证
    host: str = "127.0.0.1"
    port: int = 8000


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///./data/simple_quant.db"


class DatasourceConfig(BaseModel):
    primary: str = "akshare"
    cache_enabled: bool = True
    cache_days_daily: int = 365
    jump_threshold: float = 45.0  # 单日涨跌幅(%)超此值写入时告警（不剔除）；45 覆盖 ±20% 涨跌停、捕捉 ~-50% 拆分断崖
    tencent_min_interval: float = 0.75  # 腾讯请求最小间隔(秒)
    tencent_max_interval: float = 1.25  # 腾讯请求最大间隔(秒)，与 min 构成均匀抖动区间
    source_break_threshold: int = Field(default=3, ge=1)  # 连续 N 只源级失败触发熔断


class SyncConfig(BaseModel):
    """数据同步配置。"""

    default_start_date: str = "2021-01-04"  # 行情/宏观同步默认起始日期（回测起点）
    max_concurrent_tasks: int = Field(default=2, ge=1)  # A10: 并发同步任务上限（正整数）


class FactorsConfig(BaseModel):
    auto_discover: bool = True
    scan_package: str = "core.factors"


class StrategyConfig(BaseModel):
    max_history_runs: int = 100


class WebAppEnvSettings(BaseSettings):
    """从环境变量覆盖的设置（敏感信息）"""

    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    model_config = {"env_file": ".env", "extra": "ignore"}


class WebAppConfig(BaseModel):
    """应用配置。

    各嵌套模型自带完整默认值（与仓库 config/webapp.yaml 保持一致）：
    缺少 yaml 文件时 WebAppConfig(**{}) 仍可构造，不抛 ValidationError。
    """

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    datasource: DatasourceConfig = Field(default_factory=DatasourceConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    factors: FactorsConfig = Field(default_factory=FactorsConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)


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
    env_settings = WebAppEnvSettings()
    if env_settings.database_url:
        yaml_data.setdefault("database", {})["url"] = env_settings.database_url

    return WebAppConfig(**yaml_data)
