import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging
from sqlalchemy.orm import sessionmaker

from app.api import health, journals, photos, updates
from app.api.errors import register_error_handlers
from app.background import run_periodically
from app.config import Settings
from app.db import create_db_engine
from app.messaging.codec import order_routes
from app.messaging.sqs import SqsQueue, create_sqs_client
from app.quota import UnconfiguredAiQuota
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.ai_quota import AiQuota
from app.service.consumer import MessageQueue, QueueConsumer
from app.service.event_handlers import EventHandlers
from app.service.journals import JournalService
from app.service.photos import PhotoService, PhotoStorage
from app.service.updates import UpdateService
from app.storage.s3 import S3PhotoStorage, create_s3_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    stop = asyncio.Event()
    consuming = [
        asyncio.create_task(
            run_periodically(consumer.run_once, app.state.consumer_interval_seconds, stop)
        )
        for consumer in app.state.consumers
    ]
    yield
    stop.set()
    await asyncio.gather(*consuming)
    app.state.engine.dispose()


def create_app(
    settings: Settings | None = None,
    order_queue: MessageQueue | None = None,
    ai_quota: AiQuota | None = None,
    photo_storage: PhotoStorage | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)
    order_queue = order_queue or SqsQueue(
        create_sqs_client(settings), settings.order_placed_queue_url
    )
    photo_storage = photo_storage or S3PhotoStorage(
        create_s3_client(settings), settings.photos_bucket, settings.photo_url_ttl_seconds
    )

    app = FastAPI(title="GrowPath plant-care", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(app.state.engine))
    app.state.journal_service = JournalService(unit_of_work)
    app.state.update_service = UpdateService(
        unit_of_work,
        ai_quota or UnconfiguredAiQuota(),
        settings.feed_default_page_size,
        settings.feed_max_page_size,
    )
    app.state.photo_service = PhotoService(
        unit_of_work, photo_storage, settings.photo_url_ttl_seconds
    )

    handlers = EventHandlers(unit_of_work)
    app.state.queues = [order_queue]
    app.state.consumers = [
        QueueConsumer(
            order_queue,
            order_routes(handlers),
            frozenset(),
            settings.consumer_batch_size,
            settings.consumer_wait_seconds,
        )
    ]
    app.state.consumer_interval_seconds = settings.consumer_interval_seconds

    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(journals.router)
    app.include_router(updates.router)
    app.include_router(photos.router)
    return app
