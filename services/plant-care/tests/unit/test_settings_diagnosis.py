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
