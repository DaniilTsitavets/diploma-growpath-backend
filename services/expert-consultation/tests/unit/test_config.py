import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.unit.settings import (
    ESCALATION_QUEUE_URL,
    TICKET_TOPIC_ARN,
    UNREACHABLE_DATABASE,
    make_settings,
)


def test_settings_read_the_prefixed_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EXPERT_CONSULTATION_DATABASE_URL", UNREACHABLE_DATABASE)
    monkeypatch.setenv("EXPERT_CONSULTATION_ESCALATION_QUEUE_URL", ESCALATION_QUEUE_URL)
    monkeypatch.setenv("EXPERT_CONSULTATION_TICKET_EVENTS_TOPIC_ARN", TICKET_TOPIC_ARN)
    monkeypatch.setenv("EXPERT_CONSULTATION_AWS_REGION", "eu-west-1")

    settings = Settings()

    assert settings.escalation_queue_url == ESCALATION_QUEUE_URL
    assert settings.ticket_events_topic_arn == TICKET_TOPIC_ARN
    assert settings.aws_region == "eu-west-1"
    assert settings.service_name == "expert-consultation"


def test_the_database_url_is_a_secret():
    settings = make_settings()

    assert "nobody" not in repr(settings)
    assert settings.database_url.get_secret_value() == UNREACHABLE_DATABASE


def test_there_is_no_default_database_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EXPERT_CONSULTATION_DATABASE_URL", raising=False)

    with pytest.raises(ValidationError):
        Settings(
            escalation_queue_url=ESCALATION_QUEUE_URL,
            ticket_events_topic_arn=TICKET_TOPIC_ARN,
            _env_file=None,
        )


def test_the_ticket_ttl_defaults_to_seventy_two_hours():
    assert make_settings().ticket_ttl_seconds == 72 * 3600


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticket_ttl_seconds": 0},
        {"republish_grace_seconds": -1},
        {"maintenance_batch_size": 0},
    ],
)
def test_time_and_batch_settings_must_be_usable(overrides: dict):
    with pytest.raises(ValidationError):
        make_settings(**overrides)
