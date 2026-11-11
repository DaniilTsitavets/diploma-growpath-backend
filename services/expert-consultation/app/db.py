from fastapi import Request
from sqlalchemy import Engine, create_engine

from app.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        # `now()` arithmetic must not depend on the server setting.
        connect_args={"options": "-c timezone=UTC"},
    )


def get_engine(request: Request) -> Engine:
    return request.app.state.engine
