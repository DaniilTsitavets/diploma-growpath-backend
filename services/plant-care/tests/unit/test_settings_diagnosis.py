import pytest
from pydantic import ValidationError

from app.service.dominance import Support
from tests.unit.settings import make_settings


def test_the_dominance_thresholds_default_to_the_documented_ones():
    settings = make_settings()

    assert settings.diagnosis_dominant_support is Support.HIGH
    assert settings.diagnosis_rival_supports == {Support.MEDIUM, Support.HIGH}


def test_the_thresholds_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLANT_CARE_DIAGNOSIS_DOMINANT_SUPPORT", "medium")
    monkeypatch.setenv("PLANT_CARE_DIAGNOSIS_RIVAL_SUPPORTS", "high, low")

    settings = make_settings()

    assert settings.diagnosis_dominant_support is Support.MEDIUM
    assert settings.diagnosis_rival_supports == {Support.HIGH, Support.LOW}


def test_one_sample_at_a_temperature_above_zero_is_the_default():
    settings = make_settings()

    assert settings.diagnosis_samples == 1
    assert 0 < settings.diagnosis_sample_temperature <= 1


def test_the_samples_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLANT_CARE_DIAGNOSIS_SAMPLES", "3")

    assert make_settings().diagnosis_samples == 3


@pytest.mark.parametrize(
    "fields",
    [
        {"diagnosis_samples": 0},
        {"diagnosis_samples": -1},
        {"diagnosis_sample_temperature": 0},
        {"diagnosis_sample_temperature": 1.5},
        {"diagnosis_samples": 5},
        {"diagnosis_samples": 3, "model_bulkhead_size": 2},
    ],
)
def test_samples_and_temperature_are_validated_at_startup(fields: dict):
    with pytest.raises(ValidationError):
        make_settings(**fields)


def test_the_bulkhead_may_be_exactly_as_wide_as_the_samples():
    settings = make_settings(diagnosis_samples=2, model_bulkhead_size=2)

    assert settings.diagnosis_samples == 2


def test_the_split_mock_behaviour_is_accepted():
    assert make_settings(model_mock_behavior="split").model_mock_behavior == "split"


@pytest.mark.parametrize(
    "fields",
    [
        {"diagnosis_dominant_support": "certain"},
        {"diagnosis_dominant_support": "HIGH"},
        {"diagnosis_rival_supports": "medium,certain"},
        {"diagnosis_rival_supports": ""},
        {"diagnosis_rival_supports": "medium,,high"},
    ],
)
def test_a_threshold_that_is_not_one_of_the_three_labels_is_refused_at_startup(fields: dict):
    with pytest.raises(ValidationError):
        make_settings(**fields)
