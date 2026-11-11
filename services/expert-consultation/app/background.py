import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


async def run_periodically(
    step: Callable[[], object], interval_seconds: float, stop: asyncio.Event
) -> None:
    """Call `step` in a worker thread every interval until `stop` is set."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(step)
        except Exception:
            # A database or broker hiccup must not kill the loop; the next tick retries.
            logger.exception("background step failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass
