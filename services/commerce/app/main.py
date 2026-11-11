from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI
from growpath_observability import CorrelationIdMiddleware, configure_logging
from sqlalchemy.orm import sessionmaker

from app.api import health, items, orders
from app.api.errors import register_error_handlers
from app.config import Settings
from app.db import create_db_engine
from app.messaging.sns import SnsOrderEvents, create_sns_client
from app.payment import MockPaymentGateway
from app.repository.unit_of_work import SqlAlchemyUnitOfWork
from app.service.catalog import CatalogService
from app.service.ports import OrderEvents
from app.service.purchase import PurchaseService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    app.state.engine.dispose()


def create_app(
    settings: Settings | None = None, order_events: OrderEvents | None = None
) -> FastAPI:
    settings = settings or Settings()
    order_events = order_events or SnsOrderEvents(
        create_sns_client(settings), settings.order_placed_topic_arn
    )
    configure_logging(settings.service_name, settings.log_level)

    app = FastAPI(title="GrowPath commerce", version="0.1.0", lifespan=lifespan)
    app.state.engine = create_db_engine(settings)
    unit_of_work = partial(SqlAlchemyUnitOfWork, sessionmaker(app.state.engine))
    app.state.purchase_service = PurchaseService(unit_of_work, MockPaymentGateway(), order_events)
    app.state.catalog_service = CatalogService(unit_of_work)
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(items.router)
    app.include_router(orders.router)
    return app
