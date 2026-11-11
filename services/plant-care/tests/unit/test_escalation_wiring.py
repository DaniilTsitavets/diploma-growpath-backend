import threading

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from tests.unit.delivery_fakes import FakeEscalationEvents
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.settings import make_settings


@pytest.mark.parametrize(
    "fields",
    [
        {"escalation_ack_margin_seconds": 0},
        {"escalation_ack_margin_seconds": 86400},
        {"expert_reservation_ttl_seconds": 600, "escalation_ack_margin_seconds": 900},
    ],
)
def test_the_ack_margin_must_fit_inside_the_reservation_ttl(fields: dict):
    with pytest.raises(ValidationError, match="ack margin"):
        make_settings(**fields)


def test_the_repeater_runs_while_the_application_is_up():
    ran = threading.Event()
    app = create_app(
        make_settings(escalation_repeater_interval_seconds=0.01),
        FakeQueue(),
        photo_storage=FakePhotoStorage(),
        escalation_events=FakeEscalationEvents(),
        ticket_queue=FakeQueue(),
    )
    app.state.escalation_repeater.run_once = ran.set

    with TestClient(app):
        assert ran.wait(timeout=5)


def test_the_repeater_is_built_from_the_settings():
    settings = make_settings(
        expert_reservation_ttl_seconds=7200,
        escalation_ack_margin_seconds=300,
        escalation_repeater_grace_seconds=12,
        escalation_repeater_batch_size=3,
    )

    app = create_app(
        settings,
        FakeQueue(),
        photo_storage=FakePhotoStorage(),
        escalation_events=FakeEscalationEvents(),
    )

    repeater = app.state.escalation_repeater
    assert repeater._ttl.total_seconds() == 7200
    assert repeater._grace.total_seconds() == 12
    assert repeater._batch_size == 3
