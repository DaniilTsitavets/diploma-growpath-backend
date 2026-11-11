import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import current_user_id, get_journal_service
from app.api.schemas import JournalResponse, PlantingRequest
from app.service.journals import JournalService

router = APIRouter(prefix="/v1/journals", tags=["journals"])

UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Journals = Annotated[JournalService, Depends(get_journal_service)]


@router.get("")
def list_journals(user_id: UserId, service: Journals) -> list[JournalResponse]:
    """The journals of the user, newest first."""
    return [JournalResponse.of(view) for view in service.list_for_user(user_id)]


@router.get("/{journal_id}")
def get_journal(journal_id: uuid.UUID, user_id: UserId, service: Journals) -> JournalResponse:
    return JournalResponse.of(service.get(journal_id, user_id))


@router.put("/{journal_id}/planting")
def record_planting(
    journal_id: uuid.UUID, body: PlantingRequest, user_id: UserId, service: Journals
) -> JournalResponse:
    """Record the planting date; the checkpoint windows are computed from it."""
    return JournalResponse.of(service.plant(journal_id, user_id, body.planted_on))
