from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webapp.api.factors import router as factors_router
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
    app.include_router(factors_router)

    # 静态文件
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def root():
        return {"message": "SimpleQuant Web Dashboard", "docs": "/docs"}

    return app


app = create_app()
