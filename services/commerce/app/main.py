from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging

from app.api import health
from app.api.errors import register_error_handlers
from app.config import Settings
from app.db import create_db_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)

    app = FastAPI(title="GrowPath commerce", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
