from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webapp.api.classifications import constraints_router, router as classifications_router
from webapp.api.dashboard import router as dashboard_router
from webapp.api.factors import router as factors_router
from webapp.api.health import router as health_router
from webapp.api.macro import router as macro_router
from webapp.api.market import router as market_router
from webapp.api.settings import router as settings_router
from webapp.api.strategies import router as strategies_router
from webapp.api.universe import router as universe_router
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
    app.include_router(market_router)
    app.include_router(macro_router)
    app.include_router(universe_router)
    app.include_router(classifications_router)
    app.include_router(constraints_router)
    app.include_router(strategies_router)
    app.include_router(dashboard_router)
    app.include_router(settings_router)

    # 静态文件
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    index_html = static_dir / "index.html"

    @app.get("/")
    def root():
        # 返回 Web 看板页面（SPA）
        if index_html.exists():
            return FileResponse(str(index_html))
        return {"message": "SimpleQuant Web Dashboard", "docs": "/docs"}

    return app


app = create_app()