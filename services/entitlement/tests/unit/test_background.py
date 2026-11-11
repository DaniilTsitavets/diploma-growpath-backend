import asyncio
import logging
import threading
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.background import run_periodically
from app.main import create_app
from app.service.consumer import QueueConsumer
from tests.unit.fakes import FakeQueue, make_settings


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


def test_application_runs_a_consumer_per_queue_while_it_is_up():
    orders, tickets = FakeQueue(), FakeQueue()
    orders.send("order.placed", {})
    tickets.send("ticket.created", {})
    handled = {"order.placed": threading.Event(), "ticket.created": threading.Event()}
    app = create_app(make_settings(consumer_interval_seconds=0.01), orders, tickets)
    app.state.consumers = [
        QueueConsumer(queue, {event_type: lambda body, e=event: e.set()}, frozenset(), 5, 0)
        for queue, (event_type, event) in zip([orders, tickets], handled.items(), strict=True)
    ]

    with TestClient(app):
        assert all(event.wait(timeout=5) for event in handled.values())

    assert (len(orders.deleted), len(tickets.deleted)) == (1, 1)
