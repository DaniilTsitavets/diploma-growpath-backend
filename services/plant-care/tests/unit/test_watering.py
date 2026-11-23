import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.service.plant_profile_model import PlantProfile
from app.service.watering import (
    DEFAULT_P,
    assess_watering,
    saturation_vapour_pressure_kpa,
    vapour_pressure_deficit_kpa,
)
from app.service.watering_types import (
    Answer,
    DepletionSource,
    DryDepth,
    Flag,
    Light,
    PotDiameterClass,
    PotWeightRelative,
    PSource,
    Reliability,
    SensorUnit,
    Status,
    WateringInputs,
    WateringMethod,
    WateringObservation,
    WateringProfile,
)

TODAY = date(2026, 11, 23)
PROFILE = WateringProfile(dryness_threshold_pct=50)


def inputs(days: int | None = 6, **fields) -> WateringInputs:
    last = None if days is None else TODAY - timedelta(days=days)
    return WateringInputs(today=TODAY, last_watered_on=last, **fields)


def sensor(vwc_pct: float, days: int | None = 6, **fields) -> WateringInputs:
    return inputs(days, sensor_reading=vwc_pct, sensor_unit=SensorUnit.VWC_PCT, **fields)


def observation(days: int, depletion: float, **fields) -> WateringObservation:
    fields.setdefault("depletion_source", DepletionSource.SENSOR)
    return WateringObservation(days, depletion, **fields)


# Degradation table, one test per row.


def test_calibrated_sensor_gives_numeric_depletion_and_an_interval():
    result = assess_watering(sensor(33), PROFILE)

    assert result.d_source is DepletionSource.SENSOR
    assert result.depletion_d == 0.48
    assert result.i_rec_days == 6.25
    assert (result.i_rec_bounds.lower_days, result.i_rec_bounds.upper_days) == (5.66, 6.98)
    assert (result.i_fact_days, result.ratio_r, result.ks) == (6, 0.96, 1.0)
    assert result.status is Status.ON_TRACK
    assert result.reliability is Reliability.HIGH


def test_the_weight_in_grams_gives_an_approximate_depletion_and_an_interval():
    result = assess_watering(
        inputs(5, pot_weight_after_watering_g=1000, pot_weight_now_g=900), PROFILE
    )

    assert result.d_source is DepletionSource.WEIGHT_GRAMS
    assert result.depletion_d == 0.4
    assert result.i_rec_days == 6.25
    assert (result.i_rec_bounds.lower_days, result.i_rec_bounds.upper_days) == (5.0, 8.33)
    assert result.reliability is Reliability.HIGH


def test_the_dry_depth_and_the_diameter_give_an_interval_from_one_update():
    result = assess_watering(
        inputs(5, dry_depth=DryDepth.ABOUT_5_CM, pot_diameter_class=PotDiameterClass.FROM_10_TO_15),
        PROFILE,
    )

    assert result.d_source is DepletionSource.DEPTH
    assert result.depletion_d == 0.4
    assert result.i_rec_days == 6.25
    assert (result.i_rec_bounds.lower_days, result.i_rec_bounds.upper_days) == (4.55, 10.0)
    assert result.reliability is Reliability.MEDIUM


@pytest.mark.parametrize(
    ("fields", "days", "source", "bounds", "text", "status"),
    [
        (
            {"dry_depth": DryDepth.NOT_DRY},
            5,
            DepletionSource.DEPTH,
            (5.0, None),
            "more than 5 days",
            Status.OVERWATERING,
        ),
        (
            {"pot_weight_relative": PotWeightRelative.VERY_LIGHT},
            9,
            DepletionSource.CATEGORICAL_WEIGHT,
            (None, 9.0),
            "no more than 9 days",
            Status.ON_TRACK,
        ),
        (
            {"dry_depth": DryDepth.WHOLE_ROOT_BALL_DRY},
            9,
            DepletionSource.DEPTH,
            (None, 9.0),
            "no more than 9 days",
            Status.UNDERWATERING,
        ),
    ],
)
def test_categories_without_a_diameter_give_a_status_and_bounds_but_no_number(
    fields, days, source, bounds, text, status
):
    result = assess_watering(inputs(days, **fields), PROFILE)

    assert result.depletion_d is None and result.i_rec_days is None and result.ks is None
    assert result.d_source is source
    assert (result.i_rec_bounds.lower_days, result.i_rec_bounds.upper_days) == bounds
    assert result.i_rec_bounds.describe() == text
    assert result.status is status
    assert result.reliability is Reliability.LOW


def test_the_category_bounds_narrow_with_the_history():
    history = [observation(3, 0.0, depletion_source=DepletionSource.CATEGORICAL_WEIGHT)]

    result = assess_watering(
        inputs(9, pot_weight_relative=PotWeightRelative.VERY_LIGHT), PROFILE, history
    )

    assert result.i_rec_bounds.describe() == "between 3 and 9 days"
    assert result.calibration.n_observations == 2


def test_without_a_date_only_the_flags_are_given():
    result = assess_watering(
        inputs(None, drainage_holes=Answer.NO, dry_depth=DryDepth.NOT_DRY), PROFILE
    )

    assert result.status is Status.UNCLEAR
    assert result.i_rec_days is None and result.i_rec_bounds is None
    assert (result.i_fact_days, result.ratio_r, result.ks) == (None, None, None)
    assert result.flags == (Flag.DRAINAGE_NONE,)
    assert result.reliability is Reliability.LOW
    assert result.calibration.n_observations == 0


# Boundaries of the depletion.


def test_a_dry_soil_has_a_lower_bound_and_is_overwatered_when_watered_again():
    result = assess_watering(sensor(45, days=4), PROFILE)

    assert result.depletion_d == 0.0
    assert result.i_rec_days is None
    assert result.i_rec_bounds.describe() == "more than 4 days"
    assert result.status is Status.OVERWATERING


@pytest.mark.parametrize(
    ("vwc", "d", "has_point", "status"),
    [
        (44, 0.04, False, Status.OVERWATERING),
        (43, 0.08, True, Status.OVERWATERING),
        (42.5, 0.1, True, Status.ON_TRACK),
    ],
)
def test_the_overwatering_threshold_and_the_lower_bound_edge(vwc, d, has_point, status):
    result = assess_watering(sensor(vwc), PROFILE)

    assert result.depletion_d == d
    assert (result.i_rec_days is not None) is has_point
    assert result.status is status


@pytest.mark.parametrize(("limit", "status"), [(0.1, Status.ON_TRACK), (0.2, Status.OVERWATERING)])
def test_the_overwatering_threshold_is_a_parameter(limit, status):
    result = assess_watering(sensor(41.25), PROFILE, overwatered_depletion=limit)

    assert result.depletion_d == 0.15
    assert result.status is status


def test_at_the_target_the_depletion_is_an_upper_bound_without_stress():
    result = assess_watering(sensor(32.5, days=10), PROFILE)

    assert result.depletion_d == 0.5
    assert result.i_rec_days is None
    assert result.i_rec_bounds.describe() == "no more than 10 days"
    assert (result.ratio_r, result.ks) == (1.0, 1.0)
    assert result.status is Status.ON_TRACK


def test_past_the_target_the_stress_coefficient_drops_and_the_status_is_underwatering():
    result = assess_watering(sensor(25, days=10), PROFILE)

    assert result.depletion_d == 0.8
    assert (result.ratio_r, result.ks) == (1.6, 0.4)
    assert result.status is Status.UNDERWATERING


# The target p.


@pytest.mark.parametrize(
    ("threshold", "p", "source"),
    [
        (25, 0.25, PSource.PROFILE),
        (50, 0.5, PSource.PROFILE),
        (100, 0.8, PSource.PROFILE_CLAMPED),
        (5, 0.1, PSource.PROFILE_CLAMPED),
        (None, DEFAULT_P, PSource.DEFAULT),
    ],
)
def test_the_target_follows_the_dryness_threshold_inside_the_fao_range(threshold, p, source):
    result = assess_watering(sensor(33), WateringProfile(dryness_threshold_pct=threshold))

    assert (result.target_p, result.p_source) == (p, source)


def test_with_the_clamped_target_the_stress_uses_the_clamped_value():
    result = assess_watering(sensor(22.5), WateringProfile(dryness_threshold_pct=100))

    assert (result.depletion_d, result.target_p) == (0.9, 0.8)
    assert result.ks == 0.5


def test_a_missing_profile_defaults_p_and_caps_the_reliability():
    result = assess_watering(sensor(33), None)

    assert (result.target_p, result.p_source) == (0.5, PSource.DEFAULT)
    assert result.depletion_d == 0.48
    assert result.reliability is Reliability.MEDIUM
    assert any("No species dryness threshold" in note for note in result.assumptions)


# The choice of the signal.


def test_heavy_pot_with_a_dry_top_only_follows_the_weight_and_raises_the_flag():
    result = assess_watering(
        inputs(
            6,
            dry_depth=DryDepth.ABOUT_5_CM,
            pot_diameter_class=PotDiameterClass.FROM_10_TO_15,
            pot_weight_relative=PotWeightRelative.ABOUT_SAME,
        ),
        PROFILE,
    )

    assert Flag.TOP_DRY_HEAVY_CONFLICT in result.flags
    assert result.d_source is DepletionSource.CATEGORICAL_WEIGHT
    assert result.depletion_d is None
    assert result.status is Status.OVERWATERING


def test_the_conflict_with_grams_takes_the_depletion_from_the_grams():
    result = assess_watering(
        inputs(
            6,
            dry_depth=DryDepth.WHOLE_ROOT_BALL_DRY,
            pot_diameter_class=PotDiameterClass.LT_10,
            pot_weight_after_watering_g=1000,
            pot_weight_now_g=990,
        ),
        PROFILE,
    )

    assert Flag.TOP_DRY_HEAVY_CONFLICT in result.flags
    assert (result.d_source, result.depletion_d) == (DepletionSource.WEIGHT_GRAMS, 0.04)


def test_without_the_conflict_the_dry_depth_alone_does_not_raise_the_flag():
    result = assess_watering(
        inputs(
            6,
            dry_depth=DryDepth.ABOUT_5_CM,
            pot_diameter_class=PotDiameterClass.LT_10,
            pot_weight_relative=PotWeightRelative.NOTICEABLY_LIGHTER,
        ),
        PROFILE,
    )

    assert Flag.TOP_DRY_HEAVY_CONFLICT not in result.flags
    assert result.d_source is DepletionSource.DEPTH


@pytest.mark.parametrize(
    ("fields", "source", "depletion"),
    [
        (
            {"pot_weight_relative": PotWeightRelative.NOTICEABLY_LIGHTER},
            DepletionSource.CATEGORICAL_WEIGHT,
            None,
        ),
        ({}, DepletionSource.DEPTH, 0.2),
        (
            {"pot_weight_after_watering_g": 4000, "pot_weight_now_g": 3800},
            DepletionSource.WEIGHT_GRAMS,
            0.2,
        ),
    ],
)
def test_in_a_large_pot_the_weight_comes_before_the_depth(fields, source, depletion):
    result = assess_watering(
        inputs(
            6,
            dry_depth=DryDepth.ABOUT_5_CM,
            pot_diameter_class=PotDiameterClass.FROM_20_TO_30,
            **fields,
        ),
        PROFILE,
    )

    assert (result.d_source, result.depletion_d) == (source, depletion)


def test_the_sensor_comes_before_the_weight_and_the_weight_before_the_depth():
    full = {
        "pot_weight_after_watering_g": 1000,
        "pot_weight_now_g": 900,
        "dry_depth": DryDepth.ABOUT_5_CM,
        "pot_diameter_class": PotDiameterClass.LT_10,
    }

    assert assess_watering(sensor(33, **full), PROFILE).d_source is DepletionSource.SENSOR
    assert assess_watering(inputs(6, **full), PROFILE).d_source is DepletionSource.WEIGHT_GRAMS


@pytest.mark.parametrize(
    "fields",
    [
        {"sensor_reading": 3000, "sensor_unit": SensorUnit.RAW},
        {"sensor_reading": 30, "sensor_unit": None},
    ],
)
def test_a_sensor_reading_without_the_vwc_unit_is_a_trend_only(fields):
    result = assess_watering(inputs(6, **fields), PROFILE)

    assert result.depletion_d is None and result.d_source is DepletionSource.NONE
    assert any("trend only" in note for note in result.assumptions)
    assert result.status is Status.UNCLEAR


@pytest.mark.parametrize("reading", [-1, 101])
def test_a_vwc_reading_outside_percent_is_ignored(reading):
    assert assess_watering(sensor(reading), PROFILE).d_source is DepletionSource.NONE


# Calibration.


def test_a_partial_watering_is_not_used_for_calibration_but_the_stress_still_is():
    result = assess_watering(
        sensor(33, watering_method=WateringMethod.PARTIAL),
        PROFILE,
        [observation(4, 0.2, partial=True)],
    )

    assert result.calibration.n_observations == 0
    assert result.i_rec_days is None and result.i_rec_bounds is None
    assert (result.i_fact_days, result.depletion_d, result.ks) == (6, 0.48, 1.0)
    assert result.reliability is Reliability.MEDIUM
    assert any("partial" in note for note in result.assumptions)


def test_a_partial_observation_in_the_history_is_skipped():
    result = assess_watering(
        sensor(33), PROFILE, [observation(4, 0.3, partial=True), observation(4, 0.3)]
    )

    assert result.calibration.n_observations == 2


def test_the_history_narrows_the_corridor_and_averages_the_rate():
    single = assess_watering(sensor(33), PROFILE)

    result = assess_watering(sensor(33), PROFILE, [observation(4, 0.3)])

    assert result.calibration.n_observations == 2
    assert (result.i_rec_bounds.lower_days, result.i_rec_bounds.upper_days) == (5.71, 6.98)
    assert result.i_rec_days == 6.45
    width = result.i_rec_bounds.upper_days - result.i_rec_bounds.lower_days
    assert width < single.i_rec_bounds.upper_days - single.i_rec_bounds.lower_days


def test_observations_that_contradict_the_latest_one_are_dropped_with_a_note():
    result = assess_watering(sensor(33), PROFILE, [observation(20, 0.0)])

    assert result.calibration.n_observations == 1
    assert result.i_rec_days == 6.25
    assert any("contradict" in note for note in result.assumptions)


def test_three_observations_raise_the_reliability_of_a_categorical_estimate():
    history = [observation(4, 0.0), observation(5, 0.0)]

    result = assess_watering(inputs(6, dry_depth=DryDepth.NOT_DRY), PROFILE, history)

    assert result.calibration.n_observations == 3
    assert result.reliability is Reliability.MEDIUM


HISTORY = [observation(5, 0.4), observation(4, 0.3)]


@pytest.mark.parametrize(
    ("fields", "profile", "history", "reason", "used"),
    [
        ({"repotted_or_moved": True}, PROFILE, HISTORY, "repotted_or_moved", 1),
        ({"reset_reason": "moved to a window"}, PROFILE, HISTORY, "moved to a window", 1),
        (
            {"season_changed": True},
            WateringProfile(dryness_threshold_pct=50, dormancy_shift=True),
            HISTORY,
            "season_change",
            1,
        ),
        ({"season_changed": True}, PROFILE, HISTORY, None, 3),
        (
            {"season_changed": True},
            WateringProfile(dryness_threshold_pct=50, dormancy_shift=False),
            HISTORY,
            None,
            3,
        ),
        ({"repotted_or_moved": False}, PROFILE, HISTORY, None, 3),
        (
            {},
            PROFILE,
            [observation(5, 0.4), observation(4, 0.3, reset=True), observation(3, 0.2)],
            "observation_reset",
            2,
        ),
        (
            {"pot_diameter_class": PotDiameterClass.FROM_15_TO_20},
            PROFILE,
            [
                observation(5, 0.4, pot_diameter_class=PotDiameterClass.FROM_10_TO_15),
                observation(4, 0.3, pot_diameter_class=PotDiameterClass.FROM_15_TO_20),
            ],
            "pot_class_changed",
            2,
        ),
        (
            {"pot_diameter_class": PotDiameterClass.FROM_15_TO_20},
            PROFILE,
            [observation(5, 0.4, pot_diameter_class=PotDiameterClass.UNKNOWN)],
            None,
            2,
        ),
    ],
)
def test_the_history_is_reset_by_each_reason(fields, profile, history, reason, used):
    result = assess_watering(sensor(33, **fields), profile, history)

    assert result.calibration.reset_reason == reason
    assert result.calibration.n_observations == used


# Timing.


@pytest.mark.parametrize(("days", "i_fact"), [(-3, None), (0, 0)])
def test_a_future_or_same_day_watering_is_unclear_and_not_calibrated(days, i_fact):
    result = assess_watering(sensor(33, days=days), PROFILE, [observation(4, 0.3)])

    assert result.status is Status.UNCLEAR
    assert result.i_fact_days == i_fact
    assert result.ratio_r is None and result.ks is None
    assert result.reliability is Reliability.LOW
    if days < 0:
        assert result.i_rec_days is None and result.calibration.n_observations == 0


# Flags.


@pytest.mark.parametrize(
    ("fields", "raised"),
    [
        ({"drainage_holes": Answer.NO}, True),
        ({"standing_water_over_1h": Answer.YES}, True),
        ({"drainage_holes": Answer.YES, "standing_water_over_1h": Answer.NO}, False),
        ({"drainage_holes": Answer.UNKNOWN, "standing_water_over_1h": Answer.UNKNOWN}, False),
    ],
)
def test_no_drainage_is_a_flag(fields, raised):
    result = assess_watering(inputs(6, **fields), PROFILE)

    assert (Flag.DRAINAGE_NONE in result.flags) is raised


@pytest.mark.parametrize(
    ("lighting", "need", "raised"),
    [
        (Light.LOW, Light.BRIGHT_INDIRECT, True),
        (Light.MEDIUM, Light.DIRECT_SUN, True),
        (Light.BRIGHT_INDIRECT, Light.BRIGHT_INDIRECT, False),
        (Light.DIRECT_SUN, Light.MEDIUM, False),
        (Light.LOW, None, False),
        (None, Light.LOW, False),
    ],
)
def test_light_below_the_species_need_is_a_flag(lighting, need, raised):
    result = assess_watering(inputs(6, lighting=lighting), WateringProfile(light_need=need))

    assert (Flag.LOW_LIGHT_VS_SPECIES in result.flags) is raised


@pytest.mark.parametrize(
    ("rh", "minimum", "raised"),
    [(30, 40, True), (40, 40, False), (60, 40, False), (30, None, False), (None, 40, False)],
)
def test_dry_air_below_the_species_range_is_a_flag(rh, minimum, raised):
    result = assess_watering(inputs(6, air_rh_pct=rh), WateringProfile(rh_min_pct=minimum))

    assert (Flag.RH_BELOW_SPECIES_RANGE in result.flags) is raised


@pytest.mark.parametrize(
    ("temperature", "low", "high", "raised"),
    [
        (10, 18, 29, True),
        (35, 18, 29, True),
        (18, 18, 29, False),
        (29, 18, 29, False),
        (35, 18, None, False),
        (10, None, 29, False),
        (10, None, None, False),
        (None, 18, 29, False),
    ],
)
def test_air_outside_the_species_range_is_a_flag(temperature, low, high, raised):
    result = assess_watering(
        inputs(6, air_temp_c=temperature), WateringProfile(temp_min_c=low, temp_max_c=high)
    )

    assert (Flag.TEMP_OUTSIDE_SPECIES_RANGE in result.flags) is raised


# Vapour pressure deficit.


@pytest.mark.parametrize(
    ("temperature", "rh", "vpd"),
    [(20, 50, 1.17), (25, 40, 1.9), (30, 60, 1.7), (0, 100, 0.0), (20, 100, 0.0)],
)
def test_the_vpd_matches_the_hand_computed_values(temperature, rh, vpd):
    result = assess_watering(inputs(6, air_temp_c=temperature, air_rh_pct=rh), PROFILE)

    assert (result.air.temperature_c, result.air.rh_pct) == (temperature, rh)
    assert result.air.vpd_kpa == vpd


def test_the_saturation_pressure_matches_the_fao_table_value():
    assert saturation_vapour_pressure_kpa(20) == pytest.approx(2.338, abs=0.002)
    assert saturation_vapour_pressure_kpa(0) == pytest.approx(0.6108)
    assert vapour_pressure_deficit_kpa(20, 50) == pytest.approx(1.169, abs=0.002)


def test_the_vpd_never_changes_the_interval():
    dry_hot = assess_watering(sensor(33, air_temp_c=30, air_rh_pct=20), PROFILE)
    mild = assess_watering(sensor(33), PROFILE)

    assert dry_hot.air.vpd_kpa is not None and mild.air.vpd_kpa is None
    assert (dry_hot.i_rec_days, dry_hot.i_rec_bounds) == (mild.i_rec_days, mild.i_rec_bounds)
    assert dry_hot.ks == mild.ks


def test_an_impossible_humidity_is_ignored():
    result = assess_watering(
        inputs(6, air_temp_c=20, air_rh_pct=120), WateringProfile(rh_min_pct=40)
    )

    assert result.air.rh_pct is None and result.air.vpd_kpa is None
    assert Flag.RH_BELOW_SPECIES_RANGE not in result.flags


# Output.


def test_the_dictionary_for_the_model_is_json_with_the_agreed_keys():
    result = assess_watering(
        sensor(33, air_temp_c=20, air_rh_pct=50, drainage_holes=Answer.NO), PROFILE
    )

    data = result.to_dict()

    assert json.loads(json.dumps(data)) == data
    assert set(data) == {
        "depletion_D",
        "D_source",
        "target_p",
        "p_source",
        "I_rec_days",
        "I_rec_bounds",
        "I_fact_days",
        "ratio_r",
        "Ks",
        "status",
        "flags",
        "air",
        "calibration",
        "assumptions",
        "reliability",
    }
    assert data["air"] == {"T": 20, "RH": 50, "VPD_kPa": 1.17}
    assert data["calibration"] == {"n_observations": 1, "reset_reason": None}
    assert data["I_rec_bounds"]["lower_days"] == 5.66
    assert data["I_rec_bounds"]["text"] == "between 5.66 and 6.98 days"
    assert (data["D_source"], data["status"], data["flags"]) == (
        "sensor",
        "on_track",
        ["drainage_none"],
    )
    assert replace(result, i_rec_bounds=None).to_dict()["I_rec_bounds"] is None


def test_a_profile_is_taken_from_the_stored_plant_profile():
    stored = PlantProfile(
        slug="aloe-vera",
        source_slug="aloe-vera",
        species="Aloe Vera",
        source_name="PlantSolve",
        source_url="https://www.plantsolve.com/plants/aloe-vera/",
        license="CC BY 4.0",
        imported_on=TODAY,
        content_sha256="a" * 64,
        dryness_threshold_pct=100,
        drought_tolerance="high",
        dormancy_shift=True,
        soil_drainage="fast",
        temp_min_c=18.0,
        temp_max_c=27.0,
        rh_min_pct=50,
        light_need="bright_indirect",
    )

    profile = WateringProfile.of(stored)

    assert profile.dryness_threshold_pct == 100 and profile.dormancy_shift is True
    assert profile.light_need is Light.BRIGHT_INDIRECT
    assert (profile.temp_min_c, profile.temp_max_c, profile.rh_min_pct) == (18.0, 27.0, 50)
    assert (
        WateringProfile.of(replace(stored, light_need=None, drought_tolerance=None)).light_need
        is None
    )
