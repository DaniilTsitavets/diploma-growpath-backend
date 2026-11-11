import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging
from sqlalchemy.orm import sessionmaker

from app.api import ai_quota, entitlements, expert_quota, health
from app.api.errors import register_error_handlers
from app.background import run_periodically
from app.config import Settings
from app.db import create_db_engine
from app.messaging.codec import TICKET_EVENTS_IGNORED, order_routes, ticket_routes
from app.messaging.sqs import SqsQueue, create_sqs_client
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.consumer import QueueConsumer, Route
from app.service.entitlements import EntitlementService
from app.service.event_handlers import EventHandlers
from app.service.model import Tier
from app.service.ports import MessageQueue
from app.service.quota import QuotaService, ai_quota_kind, expert_quota_kind
from app.service.tiers import TierPolicy


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
    ticket_queue: MessageQueue | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)
    if order_queue is None or ticket_queue is None:
        sqs = create_sqs_client(settings)
        order_queue = order_queue or SqsQueue(sqs, settings.order_placed_queue_url)
        ticket_queue = ticket_queue or SqsQueue(sqs, settings.ticket_events_queue_url)

    app = FastAPI(title="GrowPath entitlement", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    unit_of_work = partial(
        SqlAlchemyUnitOfWork,
        sessionmaker(app.state.engine),
        lock_timeout=timedelta(seconds=settings.lock_timeout_seconds),
    )
    tiers = TierPolicy(settings.hobbyist_threshold_cents, settings.premium_threshold_cents)
    ai = ai_quota_kind(
        {
            Tier.STARTER: settings.assumed_ai_limit_per_day_starter,
            Tier.HOBBYIST: settings.assumed_ai_limit_per_day_hobbyist,
            Tier.PREMIUM: settings.assumed_ai_limit_per_day_premium,
        },
        timedelta(minutes=settings.ai_reservation_ttl_minutes),
    )
    expert = expert_quota_kind(
        {
            Tier.STARTER: settings.assumed_expert_limit_per_30_days_starter,
            Tier.HOBBYIST: settings.assumed_expert_limit_per_30_days_hobbyist,
            Tier.PREMIUM: settings.assumed_expert_limit_per_30_days_premium,
        },
        timedelta(hours=settings.expert_reservation_ttl_hours),
    )
    app.state.entitlement_service = EntitlementService(unit_of_work, tiers, ai, expert)
    app.state.ai_quota = QuotaService(ai, unit_of_work, tiers)
    app.state.expert_quota = QuotaService(expert, unit_of_work, tiers)

    handlers = EventHandlers(unit_of_work, app.state.expert_quota)
    app.state.queues = [order_queue, ticket_queue]
    app.state.consumers = [
        _consumer(order_queue, order_routes(handlers), frozenset(), settings),
        _consumer(ticket_queue, ticket_routes(handlers), TICKET_EVENTS_IGNORED, settings),
    ]
    app.state.consumer_interval_seconds = settings.consumer_interval_seconds

    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, settings.lock_retry_after_seconds)
    app.include_router(health.router)
    app.include_router(entitlements.router)
    app.include_router(ai_quota.router)
    app.include_router(expert_quota.router)
    return app


def _consumer(
    queue: MessageQueue, routes: Mapping[str, Route], ignored: frozenset[str], settings: Settings
) -> QueueConsumer:
    return QueueConsumer(
        queue, routes, ignored, settings.consumer_batch_size, settings.consumer_wait_seconds
    )
