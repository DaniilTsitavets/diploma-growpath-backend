import json
import re
from pathlib import Path

LABEL_FILE = Path(__file__).with_name("catalog_labels.json")
LABEL = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_LABEL_LENGTH = 40

LabelTable = dict[str, tuple[str, ...]]


def load_label_table(path: Path = LABEL_FILE) -> LabelTable:
    """Plant slug to the labels derived from its PlantSolve profile (see docs/catalog-labels.md).

    Raises ValueError when the file is unreadable or malformed: a catalog that silently lost its
    labels is worse than a service that does not start.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: cannot read the catalog labels: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object of plant slug to labels")
    return {slug: _labels_of(slug, labels, path) for slug, labels in data.items()}


def _labels_of(slug: str, labels: object, path: Path) -> tuple[str, ...]:
    if not isinstance(labels, list):
        raise ValueError(f"{path}: the labels of {slug!r} are not a list")
    for label in labels:
        valid = isinstance(label, str) and len(label) <= MAX_LABEL_LENGTH and LABEL.match(label)
        if not valid:
            raise ValueError(f"{path}: invalid label {label!r} for {slug!r}")
    if len(set(labels)) != len(labels):
        raise ValueError(f"{path}: {slug!r} lists a label twice")
    return tuple(labels)
