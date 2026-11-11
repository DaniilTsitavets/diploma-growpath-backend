from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

# Days after planting, from the example windows of US-3.1 in the BA document.
CHECKPOINT_WINDOWS = ((5, 7), (10, 12), (15, 17))


class CheckpointState(StrEnum):
    UPCOMING = "upcoming"
    OPEN = "open"
    PASSED = "passed"


@dataclass(frozen=True)
class Checkpoint:
    number: int
    opens_on: date
    closes_on: date
    state: CheckpointState


def checkpoints_for(planted_on: date | None, today: date) -> list[Checkpoint]:
    """The checkpoint windows of a planted journal; they are computed on every read."""
    if planted_on is None:
        return []
    return [
        _checkpoint(
            number, planted_on + timedelta(days=first), planted_on + timedelta(days=last), today
        )
        for number, (first, last) in enumerate(CHECKPOINT_WINDOWS, start=1)
    ]


def _checkpoint(number: int, opens_on: date, closes_on: date, today: date) -> Checkpoint:
    if today < opens_on:
        state = CheckpointState.UPCOMING
    elif today <= closes_on:
        state = CheckpointState.OPEN
    else:
        state = CheckpointState.PASSED
    return Checkpoint(number, opens_on, closes_on, state)
