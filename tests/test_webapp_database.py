import pytest

from sqlalchemy import text

from webapp.models.database import Base, SessionLocal, engine, init_db

pytestmark = pytest.mark.usefixtures("webapp_clean_state")


def test_database_engine():
    assert engine is not None


def test_session_factory():
    session = SessionLocal()
    assert session is not None
    session.close()


def test_base_class():
    assert Base is not None
    assert hasattr(Base, "metadata")


def test_init_db():
    init_db()
    # 不报错即为通过


def test_db_connection():
    session = SessionLocal()
    result = session.execute(text("SELECT 1"))
    assert result.scalar() == 1
    session.close()
