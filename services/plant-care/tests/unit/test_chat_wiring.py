from app.main import create_app
from tests.unit.fake_queue import FakeQueue
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.settings import make_settings


def build(**overrides):
    settings = make_settings(**overrides.pop("settings", {}))
    return create_app(settings, FakeQueue(), photo_storage=FakePhotoStorage(), **overrides)


def test_the_chat_is_built_from_the_settings():
    app = build(
        settings={
            "chat_top_k": 2,
            "chat_min_rank": 1.5,
            "chat_history_turns": 1,
            "chat_history_max_page_size": 7,
        }
    )

    service = app.state.chat_service
    assert (service._top_k, service._min_rank, service._history_turns, service._max_page_size) == (
        2,
        1.5,
        1,
        7,
    )


def test_the_chat_and_the_diagnosis_share_the_circuit_breaker_and_the_bulkhead():
    app = build()

    diagnosis_guard = app.state.diagnosis_service._model._guard
    assert app.state.chat_service._model._guard is diagnosis_guard


def test_the_chat_uses_the_same_effort_policy_as_the_diagnosis():
    app = build()

    assert app.state.chat_service._effort_policy is app.state.diagnosis_service._effort_policy
