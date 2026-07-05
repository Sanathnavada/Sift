"""FastAPI application factory and router composition."""

import asyncio
import sys

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import system, tasks
from .api.routes import media, music, telegram
from .lifespan import lifespan
from .middleware import register_middlewares
from .settings import STATIC_DIR
from .web import routes as ui

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def create_app() -> FastAPI:
    app = FastAPI(title="Node Services API", version="1.0.0", lifespan=lifespan)

    register_middlewares(app)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(telegram.router, prefix="/api")
    app.include_router(music.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(system.router)
    app.include_router(ui.router)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("sift.app.main:app", host="0.0.0.0", port=8000, reload=True)
