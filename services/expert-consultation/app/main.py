import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging
from sqlalchemy.orm import sessionmaker

from app.api import experts, health
from app.api.errors import register_error_handlers
from app.background import run_periodically
from app.config import Settings
from app.db import create_db_engine
from app.messaging.escalation_codec import escalation_routes
from app.messaging.sns import SnsTicketEvents, create_sns_client
from app.messaging.sqs import SqsQueue, create_sqs_client
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.consumer import QueueConsumer
from app.service.events import TicketEvents
from app.service.expert_tickets import ExpertTicketService
from app.service.maintenance import TicketMaintenance
from app.service.photos import PhotoStorage
from app.service.queue import MessageQueue
from app.service.ticket_publisher import TerminalEventPublisher
from app.service.tickets import TicketService
from app.storage.s3 import S3PhotoStorage, create_s3_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    loops = [
        asyncio.create_task(
            run_periodically(consumer.run_once, app.state.consumer_interval_seconds, stop)
        )
        for consumer in app.state.consumers
    ]
    loops.append(
        asyncio.create_task(
            run_periodically(
                app.state.maintenance.run_once, app.state.maintenance_interval_seconds, stop
            )
        )
    )
    yield
    stop.set()
    await asyncio.gather(*loops)
    app.state.engine.dispose()


def create_app(
    settings: Settings | None = None,
    escalation_queue: MessageQueue | None = None,
    ticket_events: TicketEvents | None = None,
    photo_storage: PhotoStorage | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)
    escalation_queue = escalation_queue or SqsQueue(
        create_sqs_client(settings), settings.escalation_queue_url
    )
    ticket_events = ticket_events or SnsTicketEvents(
        create_sns_client(settings), settings.ticket_events_topic_arn
    )
    photo_storage = photo_storage or S3PhotoStorage(
        create_s3_client(settings), settings.photos_bucket, settings.photo_url_ttl_seconds
    )

    app = FastAPI(title="GrowPath expert-consultation", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    app.state.queues = [escalation_queue]

    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(app.state.engine))
    terminal_events = TerminalEventPublisher(unit_of_work, ticket_events)
    app.state.ticket_service = TicketService(
        unit_of_work, ticket_events, terminal_events, settings.ticket_ttl_seconds
    )
    app.state.expert_ticket_service = ExpertTicketService(
        unit_of_work,
        photo_storage,
        settings.queue_default_page_size,
        settings.queue_max_page_size,
    )
    app.state.maintenance = TicketMaintenance(
        unit_of_work,
        ticket_events,
        terminal_events,
        settings.republish_grace_seconds,
        settings.maintenance_batch_size,
    )
    app.state.maintenance_interval_seconds = settings.maintenance_interval_seconds
    app.state.consumers = [
        QueueConsumer(
            escalation_queue,
            escalation_routes(app.state.ticket_service),
            settings.consumer_batch_size,
            settings.consumer_wait_seconds,
        )
    ]
    app.state.consumer_interval_seconds = settings.consumer_interval_seconds

    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, settings.unavailable_retry_after_seconds)
    app.include_router(health.router)
    app.include_router(experts.router)
    return app
