import asyncio
import logging
import threading
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.background import run_periodically
from app.main import create_app
from tests.unit.fakes import RecordingEvents, make_settings


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
            raise RuntimeError("database restarting")

    with caplog.at_level(logging.ERROR, logger="app.background"):
        calls = run_until(2, fail_the_first_call)

    assert calls == 2
    assert [record.message for record in caplog.records] == ["background step failed"]


def test_application_runs_the_republisher_while_it_is_up():
    ran = threading.Event()

    class Republisher:
        def run_once(self) -> int:
            ran.set()
            return 0

    app = create_app(make_settings(republish_interval_seconds=0.01), RecordingEvents())
    app.state.republisher = Republisher()

    with TestClient(app):
        assert ran.wait(timeout=5)
