from datetime import date

import pytest

from app.service.checkpoints import CheckpointState, checkpoints_for

PLANTED = date(2026, 11, 1)


def test_a_journal_that_is_not_planted_has_no_checkpoints():
    assert checkpoints_for(None, date(2026, 11, 11)) == []


def test_windows_are_counted_in_days_from_the_planting_date():
    checkpoints = checkpoints_for(PLANTED, PLANTED)

    assert [(c.number, c.opens_on, c.closes_on) for c in checkpoints] == [
        (1, date(2026, 11, 6), date(2026, 11, 8)),
        (2, date(2026, 11, 11), date(2026, 11, 13)),
        (3, date(2026, 11, 16), date(2026, 11, 18)),
    ]


@pytest.mark.parametrize(
    ("today", "states"),
    [
        (date(2026, 11, 5), ["upcoming", "upcoming", "upcoming"]),
        (date(2026, 11, 6), ["open", "upcoming", "upcoming"]),
        (date(2026, 11, 8), ["open", "upcoming", "upcoming"]),
        (date(2026, 11, 9), ["passed", "upcoming", "upcoming"]),
        (date(2026, 11, 12), ["passed", "open", "upcoming"]),
        (date(2026, 11, 19), ["passed", "passed", "passed"]),
    ],
)
def test_a_window_is_open_from_its_first_to_its_last_day(today: date, states: list[str]):
    checkpoints = checkpoints_for(PLANTED, today)

    assert [c.state for c in checkpoints] == [CheckpointState(state) for state in states]
