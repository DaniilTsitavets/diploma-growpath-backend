import uuid
from datetime import UTC, date, datetime

from app.service.diagnosis_model import DiagnosisRequest
from app.service.model import (
    Drainage,
    FingerTest,
    Lighting,
    ModelEffort,
    Photo,
    PhotoKind,
    PotWeight,
    UpdateRecord,
)


def make_update(**overrides) -> UpdateRecord:
    fields = {
        "id": uuid.uuid4(),
        "journal_id": uuid.uuid4(),
        "last_watered_on": date(2026, 11, 9),
        "watering_interval_days": 7,
        "drainage": Drainage.POOR,
        "lighting": Lighting.MEDIUM,
        "finger_test": FingerTest.WET,
        "pot_weight": PotWeight.HEAVIER,
        "sensor_reading": None,
        "created_at": datetime(2026, 11, 11, tzinfo=UTC),
    }
    return UpdateRecord(**{**fields, **overrides})


def make_request(**overrides) -> DiagnosisRequest:
    update = make_update()
    fields = {
        "species": "Basil",
        "update": update,
        "photos": [
            Photo(update.id, PhotoKind.SOIL, "key/soil"),
            Photo(update.id, PhotoKind.OVERVIEW, "key/overview"),
        ],
        "effort": ModelEffort.HIGH,
        "clarifying_question": None,
        "clarifying_answer": None,
    }
    return DiagnosisRequest(**{**fields, **overrides})
