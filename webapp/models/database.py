from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from webapp.config import get_config

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
    """初始化数据库表，确保 data 目录存在"""
    if _config.database.url.startswith("sqlite"):
        db_path = _config.database.url.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)
