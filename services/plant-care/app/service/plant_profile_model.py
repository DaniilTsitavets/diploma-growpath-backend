from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PlantProfile:
    """The facts of a plant page; every text except `species` may be absent."""

    slug: str
    source_slug: str
    species: str
    source_name: str
    source_url: str
    license: str
    imported_on: date
    content_sha256: str
    common_name: str | None = None
    scientific_name: str | None = None
    family: str | None = None
    difficulty: str | None = None
    summary: str | None = None
    quick_answer: str | None = None
    key_takeaways: tuple[str, ...] | None = None
    light: str | None = None
    sunlight_hours: str | None = None
    watering: str | None = None
    placement: str | None = None
    soil: str | None = None
    humidity: str | None = None
    temperature: str | None = None
    pruning: str | None = None
    toxicity: str | None = None
    dryness_threshold_pct: int | None = None
    drought_tolerance: str | None = None
    dormancy_shift: bool | None = None
    soil_drainage: str | None = None
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    rh_min_pct: int | None = None
    rh_max_pct: int | None = None
    light_need: str | None = None
