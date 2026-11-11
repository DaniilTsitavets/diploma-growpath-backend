import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_engine
from app.service.queue import MessageQueue, QueueUnavailable

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


class HealthStatus(BaseModel):
    status: Literal["ok"] = "ok"


def get_queues(request: Request) -> list[MessageQueue]:
    return request.app.state.queues


@router.get("/live")
def live() -> HealthStatus:
    return HealthStatus()


@router.get("/ready")
def ready(
    engine: Annotated[Engine, Depends(get_engine)],
    queues: Annotated[list[MessageQueue], Depends(get_queues)],
) -> HealthStatus:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("readiness check failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    for queue in queues:
        try:
            queue.check()
        except QueueUnavailable as exc:
            logger.error("readiness check failed", exc_info=exc)
            raise HTTPException(status_code=503, detail="message queue unavailable") from exc
    return HealthStatus()
