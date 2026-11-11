import asyncio
import logging
import threading
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.background import run_periodically
from app.main import create_app
from app.service.consumer import QueueConsumer
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import RecordingEvents
from tests.unit.settings import make_settings


def run_until(calls_needed: int, body: Callable[[int], None] = lambda call: None) -> int:
    """Run the loop until the step has been called `calls_needed` times; return the call count."""

    async def scenario() -> int:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        calls = 0

        def step() -> None:
            nonlocal calls
            calls += 1
            if calls == calls_needed:
                loop.call_soon_threadsafe(stop.set)
            body(calls)

        await asyncio.wait_for(run_periodically(step, 0.001, stop), timeout=5)
        return calls

    return asyncio.run(scenario())


def build(queue: FakeQueue | None = None, **settings):
    return create_app(
        make_settings(**settings),
        queue or FakeQueue(),
        RecordingEvents(),
        FakePhotoStorage(),
    )


def test_step_runs_repeatedly_until_stopped():
    assert run_until(3) == 3


def test_a_failing_step_is_logged_and_the_loop_keeps_going(caplog: pytest.LogCaptureFixture):
    def fail_the_first_call(call: int) -> None:
        if call == 1:
            raise RuntimeError("queue restarting")

    with caplog.at_level(logging.ERROR, logger="app.background"):
        calls = run_until(2, fail_the_first_call)

    assert calls == 2
    assert [record.message for record in caplog.records] == ["background step failed"]


def test_application_runs_the_escalation_consumer_while_it_is_up():
    queue = FakeQueue()
    queue.send("escalation.requested", {})
    handled = threading.Event()
    app = build(queue, consumer_interval_seconds=0.01)
    app.state.consumers = [
        QueueConsumer(queue, {"escalation.requested": lambda body: handled.set()}, 5, 0)
    ]

    with TestClient(app):
        assert handled.wait(timeout=5)

    assert len(queue.deleted) == 1


def test_application_runs_the_maintenance_loop_while_it_is_up():
    ran = threading.Event()

    class Maintenance:
        def run_once(self) -> None:
            ran.set()

    app = build(maintenance_interval_seconds=0.01)
    app.state.maintenance = Maintenance()

    with TestClient(app):
        assert ran.wait(timeout=5)


def test_application_consumes_the_escalation_queue_with_the_escalation_route():
    queue = FakeQueue()
    app = build(queue)

    (consumer,) = app.state.consumers

    assert set(consumer._routes) == {"escalation.requested"}
    assert app.state.queues == [queue]


def test_the_loops_take_their_settings_from_the_configuration():
    app = build(
        consumer_interval_seconds=0.25,
        maintenance_interval_seconds=7.0,
        consumer_batch_size=3,
        consumer_wait_seconds=2,
    )

    (consumer,) = app.state.consumers

    assert app.state.consumer_interval_seconds == 0.25
    assert app.state.maintenance_interval_seconds == 7.0
    assert (consumer._batch_size, consumer._wait_seconds) == (3, 2)
