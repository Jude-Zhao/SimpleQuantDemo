"""Settings API endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import make_url

from webapp.config import get_config
from webapp.models.database import engine

router = APIRouter(prefix="/api/settings", tags=["settings"])

# query 中的敏感键（忽略大小写，按键名精确匹配），值一律脱敏为 ***
_SENSITIVE_QUERY_KEYS = frozenset({"password", "passwd", "token", "secret", "key"})

# 渲染期占位符：SQLAlchemy 会把 query 值做百分号编码（*** 会变成 %2A%2A%2A），
# 因此先用安全字符占位渲染，再替换为字面 ***。
_QUERY_MASK_PLACEHOLDER = "sq-a9-masked-secret"
_QUERY_MASK = "***"
_INVALID_DATABASE_URL_DISPLAY = "[invalid database URL]"


def _display_database_url(value: str) -> str:
    """渲染用于展示/响应的数据库 URL：凭据脱敏，不回显原值、不抛异常。

    - password 渲染为 ***，username 保留；
    - 敏感 query 键的值替换为 ***；
    - 无凭据 URL（如 SQLite）原样保留定位信息；
    - 解析失败返回 "[invalid database URL]"，不回显原值。
    """
    try:
        url = make_url(value)
        query = {
            key: (_QUERY_MASK_PLACEHOLDER if str(key).lower() in _SENSITIVE_QUERY_KEYS else val)
            for key, val in url.query.items()
        }
        rendered = url.set(query=query).render_as_string(hide_password=True)
        return rendered.replace(_QUERY_MASK_PLACEHOLDER, _QUERY_MASK)
    except Exception:
        return _INVALID_DATABASE_URL_DISPLAY


class DatasourceConfig(BaseModel):
    primary: str
    cache_enabled: bool
    cache_days_daily: int


class SystemInfo(BaseModel):
    version: str
    database_url: str
    database_connected: bool


class SettingsResponse(BaseModel):
    datasource: DatasourceConfig
    system: SystemInfo


@router.get("", response_model=SettingsResponse)
def get_settings():
    """Get current system settings and status."""
    config = get_config()

    database_connected = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        database_connected = False

    return SettingsResponse(
        datasource=DatasourceConfig(
            primary=config.datasource.primary,
            cache_enabled=config.datasource.cache_enabled,
            cache_days_daily=config.datasource.cache_days_daily,
        ),
        system=SystemInfo(
            version="1.0.0",
            database_url=_display_database_url(config.database.url),
            database_connected=database_connected,
        ),
    )