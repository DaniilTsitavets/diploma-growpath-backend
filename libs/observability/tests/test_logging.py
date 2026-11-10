import json
import logging

from growpath_observability import JsonFormatter, set_correlation_id


def render(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter("svc").format(record))


def make_record(message: str = "hello", **extra) -> logging.LogRecord:
    record = logging.LogRecord("app", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_entry_contains_service_level_and_message():
    entry = render(make_record("order created"))

    assert entry["service"] == "svc"
    assert entry["level"] == "INFO"
    assert entry["message"] == "order created"


def test_entry_carries_correlation_id_and_extra_fields():
    set_correlation_id("corr-1")

    entry = render(make_record(order_id="o-7"))

    assert entry["correlation_id"] == "corr-1"
    assert entry["order_id"] == "o-7"


def test_exception_is_included():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record()
        record.exc_info = sys.exc_info()

    assert "ValueError: boom" in render(record)["exception"]
