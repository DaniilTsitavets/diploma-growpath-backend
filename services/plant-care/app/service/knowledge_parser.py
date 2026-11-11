import hashlib
import re
import uuid

from app.service.knowledge_model import DocumentDraft, SectionDraft

NS_GROWPATH_KB = uuid.UUID("8c2f6a1e-3b5d-4f7a-9e0c-5d1a2b3c4e6f")
HEADER_KEYS = ("slug", "title", "species", "source_title", "source_url")
FORBIDDEN_MARKERS = ("<kb_section", "<question")

_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")
_SECTION_HEADING = re.compile(r"^## (?P<heading>\S.*)$")
_ANCHOR_SEPARATORS = re.compile(r"[^a-z0-9]+")

TITLE_LIMIT = 200
SPECIES_LIMIT = 100
SOURCE_TITLE_LIMIT = 300
SOURCE_URL_LIMIT = 1000
HEADING_LIMIT = 200
BODY_LIMIT = 4000


class InvalidKnowledgeFile(ValueError):
    """A knowledge file does not follow the format; the service must not start with it."""


def parse_document(text: str) -> DocumentDraft:
    """Parse a knowledge file: a header between `---` lines, then sections under `## ` headings.

    The prompt wraps sections in `<kb_section>` tags, so a file that contains the tags of the
    prompt could close or forge one; such a file is rejected.
    """
    lowered = text.lower()
    for marker in FORBIDDEN_MARKERS:
        if marker in lowered:
            raise InvalidKnowledgeFile(f"the file contains the reserved text {marker!r}")
    header, body_lines = _split_header(text)
    fields = _parse_header(header)
    document_id = uuid.uuid5(NS_GROWPATH_KB, fields["slug"])
    return DocumentDraft(
        id=document_id,
        slug=fields["slug"],
        title=fields["title"],
        species=fields["species"] or None,
        source_title=fields["source_title"],
        source_url=fields["source_url"],
        content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        sections=_parse_sections(document_id, body_lines),
    )


def _split_header(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise InvalidKnowledgeFile("the file must start with a --- header")
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return lines[1:index], lines[index + 1 :]
    raise InvalidKnowledgeFile("the header is not closed with ---")


def _parse_header(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or key not in HEADER_KEYS or key in fields:
            raise InvalidKnowledgeFile(f"unexpected header line: {line!r}")
        fields[key] = _unquoted(value.strip())
    missing = [key for key in HEADER_KEYS if key not in fields]
    if missing:
        raise InvalidKnowledgeFile(f"missing header fields: {', '.join(missing)}")
    _check_header(fields)
    return fields


def _unquoted(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _check_header(fields: dict[str, str]) -> None:
    if not _SLUG.match(fields["slug"]):
        raise InvalidKnowledgeFile("the slug must match [a-z0-9-]{1,64}")
    _require_length("title", fields["title"], TITLE_LIMIT, required=True)
    _require_length("species", fields["species"], SPECIES_LIMIT)
    _require_length("source_title", fields["source_title"], SOURCE_TITLE_LIMIT, required=True)
    _require_length("source_url", fields["source_url"], SOURCE_URL_LIMIT, required=True)
    if not fields["source_url"].startswith("https://"):
        raise InvalidKnowledgeFile("source_url must start with https://")


def _require_length(name: str, value: str, limit: int, required: bool = False) -> None:
    if required and not value:
        raise InvalidKnowledgeFile(f"{name} must not be empty")
    if len(value) > limit:
        raise InvalidKnowledgeFile(f"{name} is longer than {limit} characters")


def _parse_sections(document_id: uuid.UUID, lines: list[str]) -> list[SectionDraft]:
    headings: list[str] = []
    bodies: list[list[str]] = []
    for line in lines:
        match = _SECTION_HEADING.match(line)
        if match:
            headings.append(match["heading"].strip())
            bodies.append([])
        elif bodies:
            bodies[-1].append(line)
        elif line.strip():
            raise InvalidKnowledgeFile("text before the first ## section")
    if not headings:
        raise InvalidKnowledgeFile("the file has no ## sections")

    sections: list[SectionDraft] = []
    anchors: set[str] = set()
    for position, (heading, body_lines) in enumerate(zip(headings, bodies, strict=True), start=1):
        body = "\n".join(body_lines).strip()
        anchor = _anchor(heading)
        _check_section(heading, body, anchor, anchors)
        anchors.add(anchor)
        sections.append(
            SectionDraft(uuid.uuid5(document_id, anchor), position, anchor, heading, body)
        )
    return sections


def _check_section(heading: str, body: str, anchor: str, anchors: set[str]) -> None:
    if len(heading) > HEADING_LIMIT:
        raise InvalidKnowledgeFile(f"heading {heading!r} is longer than {HEADING_LIMIT} characters")
    if not body:
        raise InvalidKnowledgeFile(f"section {heading!r} is empty")
    if len(body) > BODY_LIMIT:
        raise InvalidKnowledgeFile(f"section {heading!r} is longer than {BODY_LIMIT} characters")
    if not anchor:
        raise InvalidKnowledgeFile(f"heading {heading!r} has no letters or digits")
    if anchor in anchors:
        raise InvalidKnowledgeFile(f"two sections share the anchor {anchor!r}")


def _anchor(heading: str) -> str:
    return _ANCHOR_SEPARATORS.sub("-", heading.lower()).strip("-")
