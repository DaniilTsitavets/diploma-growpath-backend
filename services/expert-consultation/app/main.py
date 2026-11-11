from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging

from app.api import health
from app.api.errors import register_error_handlers
from app.config import Settings
from app.db import create_db_engine
from app.messaging.sqs import SqsQueue, create_sqs_client
from app.service.queue import MessageQueue


def create_app(
    settings: Settings | None = None, escalation_queue: MessageQueue | None = None
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)
    escalation_queue = escalation_queue or SqsQueue(
        create_sqs_client(settings), settings.escalation_queue_url
    )

    app = FastAPI(title="GrowPath expert-consultation", version="0.1.0")
    app.state.engine = create_db_engine(settings)
    app.state.queues = [escalation_queue]

    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app
