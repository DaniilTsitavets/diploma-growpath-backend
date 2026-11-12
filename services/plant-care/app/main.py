import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging
from sqlalchemy.orm import sessionmaker

from app.api import chat, diagnoses, escalations, health, journals, photos, updates
from app.api.errors import register_error_handlers
from app.background import run_periodically
from app.config import Settings
from app.db import create_db_engine
from app.llm.chat_factory import create_chat_model
from app.llm.factory import create_diagnosis_model, create_model_guard
from app.messaging.codec import order_routes
from app.messaging.sns import SnsEscalationEvents, create_sns_client
from app.messaging.sqs import SqsQueue, create_sqs_client
from app.messaging.ticket_codec import ticket_routes
from app.quota import create_entitlement_client
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.ai_quota import AiQuota, TierLookup
from app.service.chat import ChatService
from app.service.chat_model import ChatModel
from app.service.consumer import MessageQueue, QueueConsumer
from app.service.diagnosis import DiagnosisService
from app.service.diagnosis_model import DiagnosisModel
from app.service.dominance import DominanceRule
from app.service.effort import EffortPolicy
from app.service.escalation_events import EscalationEvents
from app.service.escalation_publisher import EscalationPublisher
from app.service.escalation_repeater import EscalationRepeater
from app.service.escalations import EscalationService
from app.service.event_handlers import EventHandlers
from app.service.expert_quota import ExpertQuota
from app.service.journals import JournalService
from app.service.knowledge_seed import KnowledgeSeeder
from app.service.photos import PhotoService, PhotoStorage
from app.service.ticket_handlers import TicketEventHandlers
from app.service.updates import UpdateService
from app.storage.s3 import S3PhotoReader, S3PhotoStorage, create_s3_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await asyncio.to_thread(app.state.seed_knowledge)
    stop = asyncio.Event()
    consuming = [
        asyncio.create_task(
            run_periodically(consumer.run_once, app.state.consumer_interval_seconds, stop)
        )
        for consumer in app.state.consumers
    ]
    repeating = asyncio.create_task(
        run_periodically(
            app.state.escalation_repeater.run_once,
            app.state.escalation_repeater_interval_seconds,
            stop,
        )
    )
    yield
    stop.set()
    await asyncio.gather(*consuming, repeating)
    for closeable in app.state.closeables:
        closeable.close()
    app.state.engine.dispose()


def create_app(
    settings: Settings | None = None,
    order_queue: MessageQueue | None = None,
    ai_quota: AiQuota | None = None,
    photo_storage: PhotoStorage | None = None,
    tier_lookup: TierLookup | None = None,
    diagnosis_model: DiagnosisModel | None = None,
    expert_quota: ExpertQuota | None = None,
    escalation_events: EscalationEvents | None = None,
    ticket_queue: MessageQueue | None = None,
    chat_model: ChatModel | None = None,
    seed_knowledge: Callable[[], object] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.service_name, settings.log_level)
    order_queue = order_queue or SqsQueue(
        create_sqs_client(settings), settings.order_placed_queue_url
    )
    ticket_queue = ticket_queue or SqsQueue(
        create_sqs_client(settings), settings.ticket_events_queue_url
    )
    photo_storage = photo_storage or S3PhotoStorage(
        create_s3_client(settings), settings.photos_bucket, settings.photo_url_ttl_seconds
    )

    app = FastAPI(title="GrowPath plant-care", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    entitlement = create_entitlement_client(settings)
    app.state.closeables = [entitlement]
    ai_quota = ai_quota or entitlement
    tier_lookup = tier_lookup or entitlement
    expert_quota = expert_quota or entitlement
    model_guard = create_model_guard(settings)
    diagnosis_model = diagnosis_model or create_diagnosis_model(
        settings,
        S3PhotoReader(
            create_s3_client(settings, presigning=False),
            settings.photos_bucket,
            settings.model_photo_max_bytes,
        ),
        model_guard,
    )
    chat_model = chat_model or create_chat_model(settings, model_guard)
    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(app.state.engine))
    app.state.seed_knowledge = (
        seed_knowledge or KnowledgeSeeder(unit_of_work, settings.knowledge_dir).run
    )
    effort_policy = EffortPolicy(tier_lookup)
    app.state.journal_service = JournalService(unit_of_work)
    app.state.update_service = UpdateService(
        unit_of_work,
        ai_quota,
        settings.feed_default_page_size,
        settings.feed_max_page_size,
    )
    app.state.photo_service = PhotoService(
        unit_of_work, photo_storage, settings.photo_url_ttl_seconds
    )
    app.state.diagnosis_service = DiagnosisService(
        unit_of_work,
        ai_quota,
        diagnosis_model,
        effort_policy,
        DominanceRule(settings.diagnosis_dominant_support, settings.diagnosis_rival_supports),
        settings.diagnosis_samples,
        settings.diagnosis_sample_temperature,
    )
    app.state.chat_service = ChatService(
        unit_of_work,
        ai_quota,
        chat_model,
        effort_policy,
        settings.chat_top_k,
        settings.chat_history_turns,
        settings.chat_history_default_page_size,
        settings.chat_history_max_page_size,
    )
    escalation_publisher = EscalationPublisher(
        unit_of_work,
        escalation_events
        or SnsEscalationEvents(create_sns_client(settings), settings.escalation_topic_arn),
        timedelta(seconds=settings.expert_reservation_ttl_seconds),
        timedelta(seconds=settings.escalation_ack_margin_seconds),
        settings.escalation_history_limit,
    )
    app.state.escalation_service = EscalationService(
        unit_of_work, expert_quota, escalation_publisher
    )
    app.state.escalation_repeater = EscalationRepeater(
        unit_of_work,
        escalation_publisher,
        expert_quota,
        timedelta(seconds=settings.escalation_repeater_grace_seconds),
        timedelta(seconds=settings.expert_reservation_ttl_seconds),
        settings.escalation_repeater_batch_size,
    )
    app.state.escalation_repeater_interval_seconds = settings.escalation_repeater_interval_seconds

    handlers = EventHandlers(unit_of_work)
    ticket_handlers = TicketEventHandlers(unit_of_work)
    app.state.queues = [order_queue, ticket_queue]
    app.state.consumers = [
        QueueConsumer(
            order_queue,
            order_routes(handlers),
            frozenset(),
            settings.consumer_batch_size,
            settings.consumer_wait_seconds,
        ),
        QueueConsumer(
            ticket_queue,
            ticket_routes(ticket_handlers),
            frozenset(),
            settings.consumer_batch_size,
            settings.consumer_wait_seconds,
        ),
    ]
    app.state.consumer_interval_seconds = settings.consumer_interval_seconds

    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app, settings.unavailable_retry_after_seconds)
    app.include_router(health.router)
    app.include_router(journals.router)
    app.include_router(updates.router)
    app.include_router(photos.router)
    app.include_router(diagnoses.router)
    app.include_router(escalations.router)
    app.include_router(chat.router)
    return app
