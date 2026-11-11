import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.service.errors import Unavailable

logger = logging.getLogger(__name__)


class PhotoStorageUnavailable(Unavailable):
    def __init__(self) -> None:
        super().__init__("the photo storage could not issue a URL")


class PhotoStorage(Protocol):
    def download_url(self, storage_key: str) -> str:
        """A presigned GET URL for the object. Raises PhotoStorageUnavailable."""
        ...


@dataclass(frozen=True)
class PhotoRef:
    kind: str
    storage_key: str


@dataclass(frozen=True)
class PhotoLink:
    kind: str
    url: str


def photo_refs(context: dict[str, Any]) -> list[PhotoRef]:
    """The photos plant-care put in the ticket snapshot; entries without a key are skipped."""
    refs = []
    for entry in context.get("photos") or []:
        if isinstance(entry, dict) and isinstance(entry.get("storage_key"), str):
            refs.append(PhotoRef(str(entry.get("kind", "")), entry["storage_key"]))
        else:
            logger.warning("ticket context holds a photo entry without a storage key")
    return refs
