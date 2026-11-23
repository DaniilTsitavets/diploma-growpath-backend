from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.service import watering_input as wi
from app.service import watering_types as wt
from app.service.model import (
    Answer,
    DryDepth,
    Lighting,
    PotDiameterClass,
    PotWeightRelative,
    SensorUnit,
    UpdateRecord,
    WateringMethod,
    YesNo,
)
from app.service.update_watering import assess_update
from app.service.watering_input import watering_history, watering_inputs
from tests.unit.diagnosis_data import make_update
from tests.unit.knowledge_fakes import FakePlantProfiles

TODAY = date(2026, 11, 11)
JOURNAL = make_update().journal_id


def update_on(day: int, **answers) -> UpdateRecord:
    """An update created on the given day of November 2026 at noon UTC."""
    created_at = datetime(2026, 11, day, 12, tzinfo=UTC)
    return make_update(journal_id=JOURNAL, created_at=created_at, **answers)


def test_every_answer_is_carried_over():
    update = make_update(
        lighting=Lighting.DIRECT_SUN,
        last_watered_on=date(2026, 11, 6),
        watering_method=WateringMethod.THROUGH_DRAINAGE,
        pot_diameter_class=PotDiameterClass.FROM_15_TO_20,
        drainage_holes=Answer.NO,
        standing_water_over_1h=Answer.YES,
        dry_depth=DryDepth.ABOUT_5_CM,
        pot_weight_relative=PotWeightRelative.NOTICEABLY_LIGHTER,
        repotted_or_moved=YesNo.NO,
        sensor_unit=SensorUnit.RAW,
        sensor_reading=Decimal("512"),
    )

    inputs = watering_inputs(update, TODAY)

    assert inputs == wt.WateringInputs(
        today=TODAY,
        last_watered_on=date(2026, 11, 6),
        watering_method=wt.WateringMethod.THROUGH_DRAINAGE,
        pot_diameter_class=wt.PotDiameterClass.FROM_15_TO_20,
        drainage_holes=wt.Answer.NO,
        standing_water_over_1h=wt.Answer.YES,
        dry_depth=wt.DryDepth.ABOUT_5_CM,
        pot_weight_relative=wt.PotWeightRelative.NOTICEABLY_LIGHTER,
        lighting=wt.Light.DIRECT_SUN,
        repotted_or_moved=False,
        sensor_reading=512.0,
        sensor_unit=wt.SensorUnit.RAW,
    )


@pytest.mark.parametrize("value", list(DryDepth))
def test_every_dry_depth_answer_maps_to_the_module_enum(value: DryDepth):
    assert watering_inputs(make_update(dry_depth=value), TODAY).dry_depth.value == value.value


@pytest.mark.parametrize("value", list(PotWeightRelative))
def test_every_relative_weight_answer_maps_to_the_module_enum(value: PotWeightRelative):
    inputs = watering_inputs(make_update(pot_weight_relative=value), TODAY)

    assert inputs.pot_weight_relative.value == value.value


@pytest.mark.parametrize("value", list(PotDiameterClass))
def test_every_pot_class_maps_to_the_module_enum(value: PotDiameterClass):
    assert watering_inputs(make_update(pot_diameter_class=value), TODAY).pot_diameter_class == (
        wt.PotDiameterClass(value.value)
    )


@pytest.mark.parametrize("value", list(WateringMethod))
def test_every_watering_method_maps_to_the_module_enum(value: WateringMethod):
    inputs = watering_inputs(make_update(watering_method=value), TODAY)

    assert inputs.watering_method.value == value.value


@pytest.mark.parametrize("value", list(Lighting))
def test_every_lighting_maps_to_the_module_enum(value: Lighting):
    assert watering_inputs(make_update(lighting=value), TODAY).lighting.value == value.value


@pytest.mark.parametrize(
    ("answer", "expected"), [(YesNo.YES, True), (YesNo.NO, False), (None, None)]
)
def test_repotting_becomes_a_nullable_boolean(answer: YesNo | None, expected: bool | None):
    assert watering_inputs(make_update(repotted_or_moved=answer), TODAY).repotted_or_moved is (
        expected
    )


def test_decimals_become_floats_and_missing_values_stay_missing():
    update = make_update(
        pot_weight_after_watering_g=Decimal("820.50"),
        pot_weight_now_g=Decimal("700"),
        sensor_unit=SensorUnit.VWC_PCT,
        sensor_reading=Decimal("35.25"),
        air_temp_c=Decimal("21.5"),
        air_rh_pct=Decimal("45.00"),
    )

    inputs = watering_inputs(update, TODAY)

    assert (inputs.pot_weight_after_watering_g, inputs.pot_weight_now_g) == (820.5, 700.0)
    assert (inputs.sensor_reading, inputs.sensor_unit) == (35.25, wt.SensorUnit.VWC_PCT)
    assert (inputs.air_temp_c, inputs.air_rh_pct) == (21.5, 45.0)
    bare = watering_inputs(make_update(last_watered_on=None), TODAY)
    assert bare.last_watered_on is None
    assert (bare.pot_weight_now_g, bare.sensor_reading, bare.sensor_unit) == (None, None, None)
    assert (bare.air_temp_c, bare.air_rh_pct) == (None, None)
    assert bare.season_changed is False


def test_the_history_is_oldest_first_and_dated_by_the_day_of_each_update():
    newest = update_on(
        10,
        last_watered_on=date(2026, 11, 3),
        dry_depth=DryDepth.ABOUT_2_3_CM,
        pot_diameter_class=PotDiameterClass.FROM_10_TO_15,
    )
    oldest = update_on(
        5,
        last_watered_on=date(2026, 11, 1),
        dry_depth=DryDepth.NOT_DRY,
        pot_diameter_class=PotDiameterClass.FROM_10_TO_15,
    )
    history = watering_history([newest, oldest])

    assert [(o.days_since_watering, o.depletion) for o in history] == [(4, 0.0), (7, 0.2)]
    assert all(o.depletion_source is wt.DepletionSource.DEPTH for o in history)
    assert all(not o.reset and not o.partial for o in history)
    assert history[0].pot_diameter_class is wt.PotDiameterClass.FROM_10_TO_15


def test_a_categorical_answer_becomes_wet_or_at_the_target():
    wet = update_on(
        5, last_watered_on=date(2026, 11, 2), pot_weight_relative=PotWeightRelative.ABOUT_SAME
    )
    dry = update_on(
        8, last_watered_on=date(2026, 11, 2), pot_weight_relative=PotWeightRelative.VERY_LIGHT
    )

    history = watering_history([dry, wet])

    assert [o.depletion for o in history] == [0.0, 1.0]
    assert {o.depletion_source for o in history} == {wt.DepletionSource.CATEGORICAL_WEIGHT}


def test_updates_that_cannot_be_turned_into_an_observation_are_skipped():
    undated = update_on(5, last_watered_on=None, dry_depth=DryDepth.NOT_DRY)
    unreadable = update_on(6, last_watered_on=date(2026, 11, 2), dry_depth=DryDepth.ABOUT_2_3_CM)

    assert watering_history([unreadable, undated]) == []


def test_a_partial_watering_is_flagged_on_its_observation():
    partial = update_on(
        8,
        last_watered_on=date(2026, 11, 6),
        dry_depth=DryDepth.NOT_DRY,
        pot_diameter_class=PotDiameterClass.LT_10,
        watering_method=WateringMethod.PARTIAL,
    )
    (observation,) = watering_history([partial])

    assert observation.partial is True


def test_a_repotting_resets_the_observation_before_it_and_keeps_its_own():
    kwargs = {"pot_diameter_class": PotDiameterClass.LT_10, "dry_depth": DryDepth.NOT_DRY}
    first = update_on(2, last_watered_on=date(2026, 11, 1), **kwargs)
    second = update_on(5, last_watered_on=date(2026, 11, 3), **kwargs)
    repotted = update_on(
        8, last_watered_on=date(2026, 11, 6), repotted_or_moved=YesNo.YES, **kwargs
    )
    after = update_on(10, last_watered_on=date(2026, 11, 9), **kwargs)

    history = watering_history([after, repotted, second, first])

    assert [o.reset for o in history] == [False, True, False, False]


def test_a_repotting_on_the_oldest_update_has_nothing_to_reset():
    oldest = update_on(
        2,
        last_watered_on=date(2026, 11, 1),
        repotted_or_moved=YesNo.YES,
        dry_depth=DryDepth.NOT_DRY,
        pot_diameter_class=PotDiameterClass.LT_10,
    )
    (observation,) = watering_history([oldest])

    assert observation.reset is False


def test_the_pot_class_change_comes_through_the_observation_for_the_module_to_cut():
    kwargs = {"dry_depth": DryDepth.NOT_DRY}
    small = update_on(
        3, last_watered_on=date(2026, 11, 1), pot_diameter_class=PotDiameterClass.LT_10, **kwargs
    )
    current = make_update(
        journal_id=JOURNAL,
        last_watered_on=date(2026, 11, 8),
        pot_diameter_class=PotDiameterClass.FROM_15_TO_20,
        dry_depth=DryDepth.ABOUT_2_3_CM,
    )
    profiles = FakePlantProfiles()
    profiles.seed("basil", species="Basil", dryness_threshold_pct=50)
    updates = FakeUpdatesOf([small])

    assessment = assess_update(profiles, updates, current, "basil", TODAY, 6)

    assert assessment.calibration.reset_reason == "pot_class_changed"
    assert assessment.calibration.n_observations == 1


class FakeUpdatesOf:
    def __init__(self, earlier: list[UpdateRecord]) -> None:
        self.earlier = earlier
        self.limits: list[int] = []

    def earlier_in_journal(self, update: UpdateRecord, limit: int) -> list[UpdateRecord]:
        self.limits.append(limit)
        return sorted(self.earlier, key=lambda row: row.created_at, reverse=True)[:limit]


def test_the_assessment_uses_the_species_profile_and_the_bounded_history():
    profiles = FakePlantProfiles()
    profiles.seed("basil", species="Basil", dryness_threshold_pct=25)
    current = make_update(
        journal_id=JOURNAL,
        last_watered_on=date(2026, 11, 8),
        pot_diameter_class=PotDiameterClass.LT_10,
        dry_depth=DryDepth.ABOUT_2_3_CM,
    )
    updates = FakeUpdatesOf([])

    assessment = assess_update(profiles, updates, current, "BASIL", TODAY, 6)

    assert assessment.target_p == 0.25
    assert assessment.p_source is wt.PSource.PROFILE
    assert assessment.i_fact_days == 3
    assert updates.limits == [6]


def test_without_a_profile_the_default_target_is_used():
    current = make_update(last_watered_on=date(2026, 11, 8))

    assessment = assess_update(FakePlantProfiles(), FakeUpdatesOf([]), current, "Basil", TODAY, 6)

    assert assessment.p_source is wt.PSource.DEFAULT


def test_a_zero_limit_does_not_read_the_history():
    updates = FakeUpdatesOf([update_on(2, last_watered_on=date(2026, 11, 1))])

    assess_update(FakePlantProfiles(), updates, make_update(), "Basil", TODAY, 0)

    assert updates.limits == []


def test_the_history_calibrates_the_interval():
    kwargs = {
        "pot_diameter_class": PotDiameterClass.FROM_10_TO_15,
        "dry_depth": DryDepth.ABOUT_2_3_CM,
    }
    earlier = [
        update_on(day, last_watered_on=date(2026, 11, day) - timedelta(days=7), **kwargs)
        for day in (3, 6)
    ]
    current = make_update(journal_id=JOURNAL, last_watered_on=date(2026, 11, 4), **kwargs)

    assessment = assess_update(
        FakePlantProfiles(), FakeUpdatesOf(earlier), current, "Basil", TODAY, 6
    )

    assert assessment.calibration.n_observations == 3


@pytest.mark.parametrize(
    ("month", "season"),
    [
        (12, wi.Season.WINTER),
        (1, wi.Season.WINTER),
        (2, wi.Season.WINTER),
        (3, wi.Season.SPRING),
        (4, wi.Season.SPRING),
        (5, wi.Season.SPRING),
        (6, wi.Season.SUMMER),
        (7, wi.Season.SUMMER),
        (8, wi.Season.SUMMER),
        (9, wi.Season.AUTUMN),
        (10, wi.Season.AUTUMN),
        (11, wi.Season.AUTUMN),
    ],
)
def test_the_season_follows_the_northern_meteorological_calendar(month: int, season: wi.Season):
    assert wi.season_of(date(2026, month, 15)) is season


@pytest.mark.parametrize(
    ("last_day", "first_day"),
    [
        (date(2026, 2, 28), date(2026, 3, 1)),
        (date(2026, 5, 31), date(2026, 6, 1)),
        (date(2026, 8, 31), date(2026, 9, 1)),
        (date(2026, 11, 30), date(2026, 12, 1)),
    ],
)
def test_the_season_changes_exactly_on_the_first_of_the_month(last_day: date, first_day: date):
    earlier = datetime.combine(last_day, datetime.min.time(), tzinfo=UTC)

    assert wi.crossed_season(earlier, last_day) is False
    assert wi.crossed_season(earlier, first_day) is True


def test_a_year_boundary_inside_the_winter_is_not_a_season_change():
    earlier = datetime(2026, 12, 20, tzinfo=UTC)

    assert wi.crossed_season(earlier, date(2027, 1, 20)) is False


def test_the_day_of_an_earlier_update_is_taken_in_utc():
    earlier = datetime(2026, 3, 1, 1, tzinfo=timezone(timedelta(hours=3)))

    assert wi.crossed_season(earlier, date(2026, 2, 28)) is False


def test_the_inputs_carry_the_season_flag():
    assert watering_inputs(make_update(), TODAY).season_changed is False
    assert watering_inputs(make_update(), TODAY, season_changed=True).season_changed is True


def seasonal_profiles(dormancy_shift: bool) -> FakePlantProfiles:
    profiles = FakePlantProfiles()
    profiles.seed("basil", species="Basil", dryness_threshold_pct=50, dormancy_shift=dormancy_shift)
    return profiles


def autumn_updates() -> list[UpdateRecord]:
    kwargs = {
        "pot_diameter_class": PotDiameterClass.FROM_10_TO_15,
        "dry_depth": DryDepth.ABOUT_2_3_CM,
    }
    return [
        update_on(day, last_watered_on=date(2026, 11, day) - timedelta(days=7), **kwargs)
        for day in (3, 6)
    ]


def current_update(last_watered_on: date) -> UpdateRecord:
    return make_update(
        journal_id=JOURNAL,
        last_watered_on=last_watered_on,
        pot_diameter_class=PotDiameterClass.FROM_10_TO_15,
        dry_depth=DryDepth.ABOUT_2_3_CM,
    )


def test_a_new_season_resets_the_calibration_of_a_dormant_species():
    assessment = assess_update(
        seasonal_profiles(True),
        FakeUpdatesOf(autumn_updates()),
        current_update(date(2026, 12, 1)),
        "Basil",
        date(2026, 12, 4),
        6,
    )

    assert assessment.calibration.reset_reason == "season_change"
    assert assessment.calibration.n_observations == 1


def test_a_new_season_keeps_the_calibration_of_a_species_without_dormancy():
    assessment = assess_update(
        seasonal_profiles(False),
        FakeUpdatesOf(autumn_updates()),
        current_update(date(2026, 12, 1)),
        "Basil",
        date(2026, 12, 4),
        6,
    )

    assert assessment.calibration.reset_reason is None
    assert assessment.calibration.n_observations == 3


def test_the_same_season_keeps_the_calibration():
    assessment = assess_update(
        seasonal_profiles(True),
        FakeUpdatesOf(autumn_updates()),
        current_update(date(2026, 11, 18)),
        "Basil",
        date(2026, 11, 20),
        6,
    )

    assert assessment.calibration.reset_reason is None
    assert assessment.calibration.n_observations == 3


def test_the_first_update_of_a_journal_has_no_season_to_change_from():
    assessment = assess_update(
        seasonal_profiles(True),
        FakeUpdatesOf([]),
        current_update(date(2026, 12, 1)),
        "Basil",
        date(2026, 12, 4),
        6,
    )

    assert assessment.calibration.reset_reason is None


def test_a_season_change_between_earlier_updates_resets_the_observation_before_it():
    kwargs = {"pot_diameter_class": PotDiameterClass.LT_10, "dry_depth": DryDepth.NOT_DRY}
    autumn = update_on(2, last_watered_on=date(2026, 11, 1), **kwargs)
    winter = make_update(
        journal_id=JOURNAL,
        created_at=datetime(2026, 12, 2, 12, tzinfo=UTC),
        last_watered_on=date(2026, 12, 1),
        **kwargs,
    )
    later = make_update(
        journal_id=JOURNAL,
        created_at=datetime(2026, 12, 9, 12, tzinfo=UTC),
        last_watered_on=date(2026, 12, 8),
        **kwargs,
    )
    newest_first = [later, winter, autumn]

    assert [o.reset for o in watering_history(newest_first, reset_on_season_change=True)] == [
        True,
        False,
        False,
    ]
    assert [o.reset for o in watering_history(newest_first)] == [False, False, False]


def test_the_first_update_has_nothing_to_reset_on_a_season_change():
    only = update_on(2, last_watered_on=date(2026, 11, 1), dry_depth=DryDepth.NOT_DRY)

    (observation,) = watering_history([only], reset_on_season_change=True)

    assert observation.reset is False
