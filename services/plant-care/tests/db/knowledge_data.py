from app.service.knowledge_model import DocumentDraft
from app.service.knowledge_parser import parse_document


def make_document(
    slug: str = "test-doc",
    species: str | None = "Basil",
    sections: dict[str, str] | None = None,
    title: str | None = None,
) -> DocumentDraft:
    """A knowledge document built through the real parser; `sections` maps heading to body."""
    sections = sections or {"Watering": "Water when the soil is dry."}
    header = (
        f"---\nslug: {slug}\ntitle: {title or slug.title()}\nspecies: {species or ''}\n"
        f"source_title: Notes\nsource_url: https://example.test/{slug}\n---\n\n"
    )
    body = "\n".join(f"## {heading}\n\n{text}\n" for heading, text in sections.items())
    return parse_document(header + body)
