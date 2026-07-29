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
    cache_days_daily: int = 365
    cache_days_minute: int = 60


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
    env_settings = WebAppEnvSettings()
    if env_settings.database_url:
        yaml_data.setdefault("database", {})["url"] = env_settings.database_url

    return WebAppConfig(**yaml_data)
