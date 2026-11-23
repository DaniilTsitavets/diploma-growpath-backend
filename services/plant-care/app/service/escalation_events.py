import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.service.errors import ContextTooLarge
from app.service.model import (
    Diagnosis,
    Escalation,
    HistoryEntry,
    Photo,
    UpdateRecord,
)

CONTEXT_LIMIT_BYTES = 64 * 1024
CONTEXT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EscalationRequested:
    """The `escalation.requested` event; its id is the escalation id, so consumers deduplicate."""

    event_id: uuid.UUID
    occurred_at: datetime
    escalation_id: uuid.UUID
    user_id: uuid.UUID
    journal_id: uuid.UUID
    update_id: uuid.UUID
    accept_until: datetime
    context: dict[str, object]


class EscalationEvents(Protocol):
    def publish_escalation_requested(self, event: EscalationRequested) -> None:
        """Raises PublishFailed when the broker does not accept the event."""
        ...


@dataclass(frozen=True)
class ContextSource:
    """Everything the expert is shown, as stored; `history` is oldest first."""

    escalation: Escalation
    species: str
    update: UpdateRecord
    photos: list[Photo]
    diagnosis: Diagnosis | None
    history: list[HistoryEntry]


def escalation_requested(
    escalation: Escalation,
    context: dict[str, object],
    expert_reservation_ttl: timedelta,
    ack_margin: timedelta,
) -> EscalationRequested:
    """Built only from the stored row and configuration, so a republished event equals the first."""
    return EscalationRequested(
        event_id=escalation.id,
        occurred_at=escalation.created_at,
        escalation_id=escalation.id,
        user_id=escalation.user_id,
        journal_id=escalation.journal_id,
        update_id=escalation.update_id,
        accept_until=escalation.created_at + expert_reservation_ttl - ack_margin,
        context=context,
    )


def context_size(context: dict[str, object]) -> int:
    """UTF-8 bytes of the compact serialization, which is how expert-consultation measures it."""
    return len(json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def build_context(source: ContextSource, max_bytes: int = CONTEXT_LIMIT_BYTES) -> dict[str, object]:
    """The ticket snapshot; when it is over the limit the oldest history entries go first."""
    context = _context(source)
    history = _history(source.history)
    context["history"] = history
    while context_size(context) > max_bytes and history:
        history.pop(0)
    if context_size(context) > max_bytes:
        raise ContextTooLarge(f"escalation {source.escalation.id} exceeds {max_bytes} bytes")
    return context


def _update(update: UpdateRecord) -> dict[str, object]:
    """The nine always-answered keys plus the optional ones, which are omitted when null."""
    optional = {
        "last_watered_on": _iso(update.last_watered_on),
        "pot_weight_after_watering_g": _decimal(update.pot_weight_after_watering_g),
        "pot_weight_now_g": _decimal(update.pot_weight_now_g),
        "repotted_or_moved": None
        if update.repotted_or_moved is None
        else update.repotted_or_moved.value,
        "sensor_reading": _decimal(update.sensor_reading),
        "sensor_unit": None if update.sensor_unit is None else update.sensor_unit.value,
        "air_temp_c": _decimal(update.air_temp_c),
        "air_rh_pct": _decimal(update.air_rh_pct),
        "air_measured_at": _iso(update.air_measured_at),
        "air_temp_min_c": _decimal(update.air_temp_min_c),
        "air_temp_max_c": _decimal(update.air_temp_max_c),
        "air_rh_min_pct": _decimal(update.air_rh_min_pct),
        "air_rh_max_pct": _decimal(update.air_rh_max_pct),
    }
    return {
        "id": str(update.id),
        "created_at": update.created_at.isoformat(),
        "lighting": update.lighting.value,
        "watering_method": update.watering_method.value,
        "pot_diameter_class": update.pot_diameter_class.value,
        "drainage_holes": update.drainage_holes.value,
        "standing_water_over_1h": update.standing_water_over_1h.value,
        "dry_depth": update.dry_depth.value,
        "pot_weight_relative": update.pot_weight_relative.value,
        **{key: value for key, value in optional.items() if value is not None},
    }


def _context(source: ContextSource) -> dict[str, object]:
    context: dict[str, object] = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "species": source.species,
        "update": _update(source.update),
        "photos": [{"kind": p.kind.value, "storage_key": p.storage_key} for p in source.photos],
    }
    if source.escalation.user_question is not None:
        context["user_question"] = source.escalation.user_question
    if source.diagnosis is not None:
        context.update(_diagnosis(source.diagnosis))
    return context


def _diagnosis(diagnosis: Diagnosis) -> dict[str, object]:
    summary = diagnosis.summary
    result: dict[str, object] = {
        "diagnosis": {
            "status": summary.status.value,
            "has_dominant_candidate": summary.has_dominant_candidate,
            "candidates": [
                {"rank": c.rank, "cause": c.cause, "rationale": c.rationale}
                for c in diagnosis.candidates
            ],
            "recommendation": summary.recommendation,
        }
    }
    if summary.clarifying_question is not None:
        result["clarifying_question"] = summary.clarifying_question
    if summary.clarifying_answer is not None:
        result["clarifying_answer"] = summary.clarifying_answer
    if summary.watering is not None:
        result["watering"] = summary.watering
    return result


def _history(entries: list[HistoryEntry]) -> list[dict[str, object]]:
    return [
        {
            "date": entry.created_at.date().isoformat(),
            "diagnosis_status": None
            if entry.diagnosis_status is None
            else entry.diagnosis_status.value,
            "top_cause": entry.top_cause,
        }
        for entry in entries
    ]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _iso(value: object) -> str | None:
    return None if value is None else value.isoformat()
