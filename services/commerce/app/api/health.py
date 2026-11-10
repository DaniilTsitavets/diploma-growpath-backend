import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import problem_response
from app.db import get_engine

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(engine: Annotated[Engine, Depends(get_engine)]):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("readiness check failed: database unavailable")
        return problem_response(503, "Service not ready", "database unavailable")
    return {"status": "ok"}
