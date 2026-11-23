import uuid
from datetime import UTC, date, datetime

from app.service.diagnosis_model import DiagnosisRequest
from app.service.model import (
    Answer,
    Lighting,
    ModelEffort,
    Photo,
    PhotoKind,
    UpdateRecord,
)


def make_update(**overrides) -> UpdateRecord:
    fields = {
        "id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "lighting": Lighting.MEDIUM,
        "last_watered_on": date(2026, 11, 9),
        "standing_water_over_1h": Answer.YES,
        "created_at": datetime(2026, 11, 11, tzinfo=UTC),
    }
    return UpdateRecord(**{**fields, **overrides})


def make_request(**overrides) -> DiagnosisRequest:
    update = make_update()
    fields = {
        "species": "Basil",
        "update": update,
        "photos": [
            Photo(update.id, PhotoKind.SYMPTOM_CLOSEUP, "key/closeup"),
            Photo(update.id, PhotoKind.OVERVIEW, "key/overview"),
        ],
        "effort": ModelEffort.HIGH,
        "clarifying_question": None,
        "clarifying_answer": None,
    }
    return DiagnosisRequest(**{**fields, **overrides})
