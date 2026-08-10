from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from webapp.config import get_config


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    Equivalent to the deprecated ``datetime.utcnow()``, but built from a
    timezone-aware clock to avoid the Python 3.12+ removal warning while
    keeping the naive-``DateTime`` column semantics unchanged.
    """
    return datetime.now(UTC).replace(tzinfo=None)


_config = get_config()

_connect_args = {}
if _config.database.url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    _config.database.url,
    connect_args=_connect_args,
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
    """初始化数据库表，确保 data 目录存在，并填充默认种子数据"""
    if _config.database.url.startswith("sqlite"):
        db_path = _config.database.url.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    # 种子数据
    from webapp.services.universe_service import seed_default_universe
    db = SessionLocal()
    try:
        seed_default_universe(db)
    finally:
        db.close()
