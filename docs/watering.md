# Watering assessment

The assessment of one plant update is a pure calculation: no I/O, no model call. It lives in
`services/plant-care/app/service/watering.py` (entry point `assess_watering`) with helpers
`watering_depletion.py` (step A), `watering_calibration.py` (step C) and the types in `watering_types.py`.
The species facts it needs are stored in `plant_profiles` (see [db/plant-care.md](db/plant-care.md)) and are
parsed from the PlantSolve record by `app/knowledge_import/watering_facts.py`.

The output is `WateringAssessment`; `to_dict()` is what the model prompt receives. The model must word
the answer from `reliability`, `I_rec_bounds` and `assumptions`: a categorical-only estimate is never exact.

## 1. Species facts parsed at import

All columns are nullable. A value that cannot be read reliably is stored as NULL, never guessed. The content
hash of the profile includes these values, so a re-import replaces a row when any of them changes.

| Column | Source in the PlantSolve record | Rule |
|---|---|---|
| `dryness_threshold_pct` | `plantHero.drynessThreshold` | integer `25`, `50` or `100`; anything else (including a string `"50"` or a boolean) is NULL |
| `drought_tolerance` | `plantHero.droughtTolerance` | `low`, `medium`, `high` (trimmed, lower-cased); anything else is NULL |
| `dormancy_shift` | `plantHero.dormancyShift` | a JSON boolean only; anything else is NULL |
| `soil_drainage` | `plantHero.soilDrainage` | `fast` or `good`; anything else is NULL |
| `light_need` | `plantHero.lightMin` | `low` to `low`, `medium` to `medium`, `bright` to `bright_indirect`, `full` to `direct_sun`; anything else is NULL |
| `temp_min_c`, `temp_max_c` | `parameters.temperature.recommended`, else `plantHero.temperature` | see 1.1 |
| `rh_min_pct`, `rh_max_pct` | `parameters.humidity.recommended`, else `plantHero.humidity` | see 1.2 |

`light_need` is the dimmest light the plant is documented to live in (`lightMin`), not its preferred light:
the flag `low_light_vs_species` should fire only when the pot gets less light than the plant tolerates. The
preferred light stays in the text column `light`. The ordinal is `low < medium < bright_indirect < direct_sun`.

The temperature and humidity ranges are the *recommended* ranges. The *survivable* ranges are not stored.
The `parameters.*.recommended` text is tried first, the `plantHero` text second: the first one that parses wins.
Five of the 15 imported plants have no `parameters.humidity` (cactus, fiddle leaf fig, monstera, snake plant, ZZ plant)
and aloe vera has no `parameters` at all, so they use the `plantHero` text; for snake plant and ZZ plant that text is
`low-medium`, so their humidity range is NULL.

### 1.1 Temperature text

Examples from the real records: `65-85°F (18-29°C)`, `16°C to 24°C (60°F to 75°F)`, `18-24°C (65-75°F)`,
`65°F - 80°F (18°C - 27°C)`.

- A range is `<number> [°C|°F] <-, en dash, em dash or "to"> <number>°<C|F>`; the unit after the second number
  decides the unit of both.
- If the text has exactly one distinct Celsius range, it is used as written (no conversion).
- Otherwise, if it has no Celsius range and exactly one Fahrenheit range, it is converted with
  `C = (F - 32) * 5 / 9` and rounded to one decimal (`65-80°F` gives `18.3-26.7`).
- Negative numbers are read (`10-95°F (-12-35°C)` gives the Celsius range `-12..35`).

NULL (both columns) when: the text is absent or not a string; there is no range with a degree unit
(`warm`, `18-24 degrees`); two different Celsius ranges (or two Fahrenheit ranges without a Celsius one) appear
(`65-75°F in summer, 55-65°F in winter`); the minimum exceeds the maximum; a bound is outside -50..60 °C.

### 1.2 Humidity text

Examples: `40-60%`, `60% - 80%`, `50% to 80%`, `50-60%+`, `low-medium`.

- A range is `<integer> [%] <-, en dash, em dash or "to"> <integer>%`.
- Exactly one distinct range must be present. `50-60%+` means "50-60% or more": `rh_min_pct` is 50 and
  `rh_max_pct` is NULL (open-ended).

NULL (both columns) when: the text is absent; it has only words (`low-medium`, `Medium to high`, `low`); it has a
single number (`recommended 30%`); it has two different ranges; the minimum exceeds the maximum; a bound is
outside 0..100. Words are never translated to numbers.

## 2. Inputs, profile, history

`WateringInputs` carries the questionnaire answers (all optional; `today` is the day of the assessment) and the
caller's reset hints (`season_changed`, `reset_reason`). It uses plain enums defined in `watering_types.py`; the
integration step maps the questionnaire enums to them. `WateringProfile` holds the species facts above
(`WateringProfile.of(plant_profile)` builds it from the stored profile). `WateringObservation` is one earlier
assessment: days since watering, depletion D, its source, whether the watering was partial, whether it marks a reset,
and the pot class at that time. The history is ordered from oldest to newest. A categorical observation stores D as
`0.0` (wet) or `1.0` (at or past the target).

## 3. Algorithm

Notation: D is the depletion (0 = just watered, 1 = the root zone is at the floor), p is the target depletion,
t is the number of days since the last watering, I_rec is the recommended interval between waterings.

### Step A. Depletion D (first usable signal wins)

1. **Soil sensor**, only with `sensor_unit = vwc_pct`: `D = (θ_full − θ) / (θ_full − θ_floor)`, with θ the reading
   divided by 100, `θ_floor = 0.20`, `θ_full = 0.45`, clamped to 0..1. A reading outside 0..100 is ignored. A reading
   with unit `raw` (or with no unit) gives a trend only: **no numeric D is computed from it**, and the output says so
   in `assumptions`.
2. **Weight in grams**, both values required: `D = (W_full − W) / (W_full − W_dry_ref)` with
   `W_dry_ref = 0.75 * W_full`, so `D = (W_full − W) / (0.25 * W_full)`, clamped to 0..1. Both weights must be positive.
3. **Dry depth** with a known pot class: `D = depth / H`; `H` is the middle of the diameter class (`lt_10` 8,
   `10_15` 12.5, `15_20` 17.5, `20_30` 25, `gt_30` 35 cm); depth is `not_dry` 0, `about_2_3cm` 2.5, `about_5cm` 5,
   `whole_root_ball_dry` H (so D = 1), clamped to 0..1.
4. **Categorical** (no number). Without a pot class the depth answers give a level: `not_dry` is *wet* (D close to 0),
   `whole_root_ball_dry` is *very dry* (D above any p). `about_2_3cm` and `about_5cm` give nothing. The weight
   answers give: `about_same` is *wet*, `very_light` is *at or past the target* (D at least p), `noticeably_lighter`
   gives nothing. Order: depth levels, then weight levels.

**Large pots** (`20_30`, `gt_30`): weight is the main signal. The order is sensor, grams, categorical weight, and the
dry depth is used only when no weight answer exists at all (a `noticeably_lighter` answer counts as a weight answer).

**Conflict.** The dry depth is *deep* when it is `about_5cm` or `whole_root_ball_dry`. If it is deep and the weight says
wet (`about_same`, or grams giving D below 0.05), only the top layer is dry: D comes from the weight and the flag
`top_dry_heavy_conflict` is raised. The flag is raised even when a sensor wins.

`D_source` is `sensor`, `weight_grams`, `depth`, `categorical_weight` or `none`. For `depth` or `categorical_weight`
without a number, `depletion_D` is null and only bounds are given.

### Step B. Target p

`p = clamp(dryness_threshold_pct / 100, 0.1, 0.8)`. `p_source` is `profile`, `profile_clamped` (the threshold was
outside 0.1..0.8, so 100% gives 0.8) or `default`. Without a profile, or without a threshold, `p = 0.5` and
`p_source = default`.

### Step C. Drying rate and recommended interval

Used only when the last watering date is known, `t > 0`, and `watering_method` is not `partial` (a partial watering
is not a full one, so the interval since it says nothing about the drying rate). Each observation bounds I_rec,
assuming a constant drying rate `k = D / t`:

| Observation | Result |
|---|---|
| `D < 0.05` (D close to 0) | `I_rec > t` (lower bound) |
| `0.05 <= D < p` | `k = D / t`, `I_rec = p * t / D`, within `p*t/(D+e) .. p*t/(D−e)` (`e` is the tolerance of the source; no upper limit when `D <= e`) |
| `D >= p` | `I_rec <= t` (upper bound) |

The tolerance `e` of D is 0.05 for the sensor, 0.10 for grams, 0.15 for depth (unverified assumptions).
The history narrows the corridor: every usable observation gives an interval, the corridor is their intersection and
the point estimate is `p / mean(k)`, kept inside the corridor. If the intersection is empty, the observations
contradict each other and only the latest one is used (a note is added to `assumptions`).

History is cut (the cut observations and everything before them are dropped) on:

- the caller's `reset_reason` (any text; it is returned as `calibration.reset_reason`);
- `repotted_or_moved = true` (`repotted_or_moved`);
- `season_changed = true` when the species has `dormancy_shift = true` (`season_change`); the caller decides when the
  season changed, the calculation only applies it;
- an observation with `reset = true` (`observation_reset`), which is dropped together with everything before it;
- an observation made with another known pot class (`pot_class_changed`), dropped with everything before it.

Partial observations and observations with `days <= 0` or D outside 0..1 are skipped, without cutting.

### Step D. Deviation and stress

`I_fact` is the number of days between `last_watered_on` and `today`. `r = I_fact / I_rec`. When there is no point
estimate of I_rec but D is known, `r = D / p` (the same value under the constant-rate assumption). The stress
coefficient is `Ks = 1` if `r <= 1`, otherwise `Ks = clamp((1 − r * p) / (1 − p), 0, 1)` (FAO-56 equation 84, written
with r; guarded for `p = 1`). For a known D above p this is `(1 − D) / (1 − p)`.

### Flags

Independent of the date:

- `drainage_none`: `drainage_holes = no` or `standing_water_over_1h = yes`;
- `low_light_vs_species`: `lighting` is below `light_need` on the ordinal;
- `rh_below_species_range`: `air_rh_pct` is below `rh_min_pct` (a value outside 0..100 is ignored);
- `temp_outside_species_range`: `air_temp_c` is below `temp_min_c` or above `temp_max_c` (either bound may be missing);
- `top_dry_heavy_conflict`: see Step A.

### Status

`status` is one of `underwatering`, `overwatering`, `on_track`, `unclear`. The status describes the interval that ends
today and assumes the pot is watered at this update (so D is the depletion at the moment of watering).

1. No date, a future date, or `t = 0`: `unclear`.
2. Wet (D below `OVERWATERED_DEPLETION = 0.1`, or a *wet* level): `overwatering` (the pot was watered while still wet).
3. A *very dry* level, or `Ks < 1`: `underwatering`.
4. A numeric Ks (or the *at or past the target* level): `on_track`.
5. Otherwise: `unclear`.

`OVERWATERED_DEPLETION` is the author's threshold and is a parameter of `assess_watering`
(`overwatered_depletion`).

### Degradation table

| Data | Result |
|---|---|
| calibrated soil sensor (`vwc_pct`) and a date | numeric D and an interval in days (point and corridor) |
| date and (depth with the pot diameter, or weight in grams) | approximate D; the interval with a tolerance already from one update when `0.05 <= D < p` |
| date and categories only, no pot diameter | status and bounds (`more than 5 days`, `no more than 9 days`) that narrow with the history |
| no date | flags only: status `unclear`, no interval, no `Ks`, `reliability` low (D is still reported if it can be computed) |

### Reliability

`high`: sensor or grams. `medium`: numeric depth. `low`: categorical only, no signal, no date or `t <= 0`.
Three or more used observations raise it by one level. Without a species threshold (`p_source = default`) it is
at most `medium`; a partial last watering caps it at `medium`.

### Air context

`air = {T, RH, VPD_kPa}`. VPD is context for the model only and **never scales an interval**:
`e_sat(T) = 0.6108 * exp(17.27 * T / (T + 237.3))` (kPa), `VPD = e_sat * (1 − RH / 100)`, rounded to 2 decimals.
Hand-computed values used by the tests: 20 °C and 50% give 1.17 kPa, 25 °C and 40% give 1.90 kPa, 30 °C and 60%
give 1.70 kPa, 100% humidity gives 0.

Rounding: depletion, `I_rec`, bounds, `r` and `Ks` are rounded to 2 decimals.

## 4. Constants and assumptions

| Constant | Value | Source |
|---|---|---|
| `e_sat` formula | 0.6108, 17.27, 237.3 | FAO Irrigation and Drainage Paper 56, equation 11 |
| `Ks` formula | `(1 − r·p) / (1 − p)` | FAO-56 equation 84 (`(TAW − Dr) / ((1 − p) TAW)`) rewritten with r = I_fact / I_rec |
| `P_MIN`, `P_MAX` | 0.1, 0.8 | range of the allowable depletion p, as cited by the author from FAO-56; not re-verified |
| `DEFAULT_P` | 0.5 | the author's default; unverified assumption |
| `THETA_FLOOR` | 0.20 | the author's value for peat substrates; not verified |
| `THETA_FULL` | 0.45 | unverified assumption (volumetric water content of a freely drained potting mix) |
| `WEIGHT_DRY_REF_FRACTION` | 0.75 | unverified assumption: water is about 45% of the full pot weight, and the part above the sensor floor (about 56% of it) is lost at D = 1 |
| depth classes 0 / 2.5 / 5 cm | 0, 2.5, 5 | extension guides, as cited by the author; not verified |
| `H` (root zone height) | middle of the pot diameter class (8, 12.5, 17.5, 25, 35 cm) | the author's assumption |
| `DRY_EPSILON` | 0.05 | unverified assumption: D below it counts as "close to 0" in step C |
| `OVERWATERED_DEPLETION` | 0.1 | the author's threshold (a parameter) |
| tolerance `e` of D | 0.05 sensor, 0.10 grams, 0.15 depth | unverified assumptions |
| `HISTORY_RELIABILITY_MIN` | 3 | unverified assumption |

The author's assumptions, repeated in `assumptions` of the output where they apply:

- `p` is about the species dryness threshold (`drynessThreshold` of PlantSolve);
- the root zone is as high as the middle of the pot diameter class;
- the drying rate is constant between waterings;
- the last watering counts as a full one unless `watering_method = partial`;
- the update is made at the moment the pot is (or would be) watered, so the status refers to the interval that ends today.

Decisions made while writing this module (the specification said "decide"):

- the dry reference weight is a fixed share (75%) of the weight after watering, because only two weights are asked;
- a categorical answer is a bound, never a number (`depletion_D` is null);
- a *deep* dry depth is `about_5cm` or `whole_root_ball_dry`;
- the pot class of an observation is compared only when both classes are known and not `unknown`;
- a sensor, a depth or a weight that gives D for a partial watering still gives `Ks` and the status, but the observation
  is not added to the calibration.

## 5. Integration

The diagnosis attempt (`DiagnosisService`) computes the assessment before the model call and gives the model a computed
fact with its basis instead of the raw questionnaire alone. The code is `app/service/watering_input.py` (mapping),
`app/service/update_watering.py` (`assess_update`, the function that reads through the repository ports) and
`DiagnosisService._assess_watering`.

### Inputs

- `today` is the UTC date of the attempt (`DiagnosisService` takes a clock, so tests fix it).
- The update's questionnaire enums are mapped to the module enums by value; `Decimal` measurements become floats and a
  missing value stays `None`. `season_changed` and `reset_reason` are not set: the service does not decide when a
  season changed.
- The species profile is found by the journal's `species` with `PlantProfileRepository.get_by_species`, a
  case-insensitive match of `plant_profiles.species` (the lowest slug when two profiles share a species). No profile
  gives `profile = None`, which is not an error: `p` is the default 0.5 and the species flags do not fire.

### History

- The earlier updates of the same journal, newest first, at most `PLANT_CARE_WATERING_HISTORY_LIMIT` (default 6;
  0 reads none), through `UpdateRepository.earlier_in_journal`.
- Each one becomes a `WateringObservation` with its own inputs and its own day (`created_at` in UTC) as `today`. It uses
  step A only (`estimate_depletion`), because an observation needs only the days since watering, D and its source; a
  categorical answer is stored as 0.0 (wet) or 1.0 (at or past the target), as the module expects. An update without
  `last_watered_on`, or with no usable signal, gives no observation.
- Reset rules: an update that answers `repotted_or_moved = yes` marks the observation just before it as a reset
  (`reset = true`), so the module cuts everything up to and including it; the repotting update keeps its own
  observation. The same answer on the update being assessed is applied by the module (`reset_reason`). A change of
  `pot_diameter_class` between known classes is cut by the module through the observation's pot class.

### Prompt and storage

- The user prompt gets a `<watering_assessment>` block with `WateringAssessment.to_dict()` as JSON (angle brackets
  escaped like every other block), after `<questionnaire>`. The system prompt tells the model that the block is data,
  that `status` and `flags` win over guesses from the raw answers, that a `low` reliability or a categorical-only
  estimate is never presented as exact (and what data would sharpen it), and that the raw questionnaire stays the source
  for everything the assessment does not cover. The JSON schema of the model's answer is unchanged.
- The same dictionary is stored in `diagnoses.watering_assessment` (migration `0007`) by the write that finishes the
  attempt: the result of a completed or `awaiting_clarification` diagnosis, and also `degraded`. It is overwritten by
  the next attempt.
- If the profile or history lookup cannot reach the database (`Unavailable`), the attempt continues without an
  assessment: no block in the prompt and `watering = null`.

### Clarification

A clarification re-evaluation reuses the stored assessment of the first attempt, so the model sees the same facts it was
asked the question with and the interval does not shift with the day (a stored assessment depends on `today`). A
diagnosis made before the column existed has none stored and is assessed at the re-evaluation. A retry after `degraded`
or a lost reservation is a new attempt and computes a fresh assessment.

### API

`DiagnosisResponse` (the feed item and the diagnosis resource) has `watering`: the dictionary above, typed as
`WateringResponse` in `contracts/openapi/plant-care.yaml`, or `null` for a diagnosis made before the assessment existed
or when it could not be computed. Keys: `status`, `reliability`, `depletion_D`, `D_source`, `target_p`, `p_source`,
`I_rec_days`, `I_rec_bounds {lower_days, upper_days, text}`, `I_fact_days`, `ratio_r`, `Ks`, `flags[]`,
`air {T, RH, VPD_kPa}`, `calibration {n_observations, reset_reason}`, `assumptions[]`.
The escalation context sent to the expert (snapshot v2) does not include it.
