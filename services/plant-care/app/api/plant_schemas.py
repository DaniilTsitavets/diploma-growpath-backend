from datetime import date

from pydantic import BaseModel

from app.service.plant_profile_model import PlantProfile


class PlantSourceResponse(BaseModel):
    name: str
    url: str
    license: str


class FaqEntryResponse(BaseModel):
    question: str
    answer: str


class PlantProfileResponse(BaseModel):
    """Absent facts, an absent FAQ and empty labels are left out of the JSON, not sent as null."""

    slug: str
    species: str
    sources: list[PlantSourceResponse]
    updated_at: date
    common_name: str | None = None
    scientific_name: str | None = None
    family: str | None = None
    difficulty: str | None = None
    summary: str | None = None
    quick_answer: str | None = None
    key_takeaways: list[str] | None = None
    light: str | None = None
    sunlight_hours: str | None = None
    watering: str | None = None
    placement: str | None = None
    soil: str | None = None
    humidity: str | None = None
    temperature: str | None = None
    pruning: str | None = None
    toxicity: str | None = None
    labels: list[str] | None = None
    faq: list[FaqEntryResponse] | None = None

    @classmethod
    def of(cls, profile: PlantProfile) -> "PlantProfileResponse":
        converted = {"sources", "updated_at", "key_takeaways", "labels", "faq"}
        facts = {name: getattr(profile, name) for name in cls.model_fields if name not in converted}
        source = PlantSourceResponse(
            name=profile.source_name, url=profile.source_url, license=profile.license
        )
        return cls(
            **facts,
            sources=[source],
            updated_at=profile.imported_on,
            key_takeaways=None if profile.key_takeaways is None else list(profile.key_takeaways),
            labels=list(profile.labels) or None,
            faq=None
            if profile.faq is None
            else [FaqEntryResponse(question=e.question, answer=e.answer) for e in profile.faq],
        )
