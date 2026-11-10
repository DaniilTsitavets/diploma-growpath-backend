from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging

from app.api import health
from app.api.errors import register_error_handlers
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.service_name, settings.log_level)

    app = FastAPI(title="GrowPath commerce", version="0.1.0")
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
