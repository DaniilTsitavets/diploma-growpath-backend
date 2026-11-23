import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import current_user_id
from app.api.problems import identity_required, invalid_request, not_found, problem, responses
from app.service.model import PhotoKind
from app.service.photos import PhotoService, UpdatePhotos

router = APIRouter(prefix="/v1/updates/{update_id}/photos", tags=["photos"])


def get_photo_service(request: Request) -> PhotoService:
    return request.app.state.photo_service


UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Photos = Annotated[PhotoService, Depends(get_photo_service)]


LIST_ERRORS = responses(
    identity_required(),
    not_found("The user has no such update"),
    invalid_request("A path parameter is invalid"),
)
UPLOAD_ERRORS = responses(
    LIST_ERRORS,
    problem(409, "No roots photo was asked for", "roots-photo-not-requested"),
    invalid_request(
        "A path or body parameter is invalid, or the type or size is not allowed",
        "photo-too-large",
        "photo-type-not-allowed",
    ),
)
CONFIRM_ERRORS = responses(
    LIST_ERRORS,
    problem(
        409,
        "No roots photo was asked for, or the object was not uploaded",
        "roots-photo-not-requested",
        "photo-not-uploaded",
    ),
    invalid_request(
        "The uploaded object is too large or of a type that is not allowed; it was deleted",
        "photo-too-large",
        "photo-type-not-allowed",
    ),
)


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PhotoKind
    content_type: str
    size_bytes: int = Field(ge=1)


class UploadResponse(BaseModel):
    kind: PhotoKind
    upload_url: str
    expires_in_seconds: int
    required_headers: dict[str, str]


class PhotoResponse(BaseModel):
    kind: PhotoKind
    url: str


class PhotosResponse(BaseModel):
    photos: list[PhotoResponse]
    missing_required: list[PhotoKind]

    @classmethod
    def of(cls, photos: UpdatePhotos) -> "PhotosResponse":
        return cls(
            photos=[PhotoResponse(kind=link.kind, url=link.url) for link in photos.photos],
            missing_required=photos.missing_required,
        )


@router.post("", responses=UPLOAD_ERRORS)
def request_upload(
    update_id: uuid.UUID, body: UploadRequest, user_id: UserId, service: Photos
) -> UploadResponse:
    """A presigned PUT URL for one photo of the update; nothing is stored until it is confirmed.

    The URL signs the declared type and size: the upload must send exactly `required_headers`.
    Repeating the request issues a new URL; for a confirmed kind the photo is replaced by the
    next confirmation. A `roots_drainage` photo is accepted only while the diagnosis awaits a
    clarification in which the AI asked for it (409 otherwise).
    """
    upload = service.request_upload(
        update_id, user_id, body.kind, body.content_type, body.size_bytes
    )
    return UploadResponse(
        kind=upload.kind,
        upload_url=upload.url,
        expires_in_seconds=upload.expires_in_seconds,
        required_headers={
            "Content-Type": upload.content_type,
            "Content-Length": str(upload.size_bytes),
        },
    )


@router.post("/{kind}/confirm", responses=CONFIRM_ERRORS)
def confirm_upload(
    update_id: uuid.UUID, kind: PhotoKind, user_id: UserId, service: Photos
) -> PhotoResponse:
    """Check the uploaded object and record the photo; confirming again changes nothing.

    The object must exist (409 `photo-not-uploaded` otherwise) and be within the size and type
    limits; an object that is not is deleted (422).
    """
    link = service.confirm_upload(update_id, user_id, kind)
    return PhotoResponse(kind=link.kind, url=link.url)


@router.get("", responses=LIST_ERRORS)
def list_photos(update_id: uuid.UUID, user_id: UserId, service: Photos) -> PhotosResponse:
    """The photos of the update with presigned GET URLs and the required kinds still missing."""
    return PhotosResponse.of(service.list_photos(update_id, user_id))
