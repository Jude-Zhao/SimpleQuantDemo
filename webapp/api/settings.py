"""Settings API endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from webapp.config import get_config
from webapp.models.database import engine

router = APIRouter(prefix="/api/settings", tags=["settings"])


class DatasourceConfig(BaseModel):
    primary: str
    secondary: str
    cache_enabled: bool
    cache_days_daily: int
    cache_days_minute: int


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
            secondary=config.datasource.secondary,
            cache_enabled=config.datasource.cache_enabled,
            cache_days_daily=config.datasource.cache_days_daily,
            cache_days_minute=config.datasource.cache_days_minute,
        ),
        system=SystemInfo(
            version="1.0.0",
            database_url=config.database.url,
            database_connected=database_connected,
        ),
    )