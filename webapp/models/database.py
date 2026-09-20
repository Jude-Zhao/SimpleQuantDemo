from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from webapp.config import get_config


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    Equivalent to the deprecated ``datetime.utcnow()``, but built from a
    timezone-aware clock to avoid the Python 3.12+ removal warning while
    keeping the naive-``DateTime`` column semantics unchanged.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


_config = get_config()

_is_sqlite = _config.database.url.startswith("sqlite")

_connect_args = {}
if _is_sqlite:
    _connect_args["check_same_thread"] = False
    # 写锁等待上限（秒）。sqlite3 默认 5s：同步/策略/宏观后台线程与 Web
    # 请求线程并发写同一库文件时，1000 行批量 upsert 持锁期间竞争方极易
    # 超时抛 database is locked，30s 让偶发竞争在等待中化解。
    _connect_args["timeout"] = 30.0

engine = create_engine(
    _config.database.url,
    connect_args=_connect_args,
    echo=False,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        # WAL 使读写不互斥（rollback journal 下读锁挡写、写锁挡读，多线
        # 程并发访问极易 database is locked）；NORMAL 是 WAL 下安全的耐久
        # 级别。journal_mode 持久化于库文件，逐连接重复设置幂等无害。
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

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
