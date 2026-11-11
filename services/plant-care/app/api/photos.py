import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import current_user_id
from app.service.model import PhotoKind
from app.service.photos import PhotoService, UpdatePhotos

router = APIRouter(prefix="/v1/updates/{update_id}/photos", tags=["photos"])


def get_photo_service(request: Request) -> PhotoService:
    return request.app.state.photo_service


UserId = Annotated[uuid.UUID, Depends(current_user_id)]
Photos = Annotated[PhotoService, Depends(get_photo_service)]


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PhotoKind


class UploadResponse(BaseModel):
    kind: PhotoKind
    upload_url: str
    expires_in_seconds: int


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


@router.post("")
def request_upload(
    update_id: uuid.UUID, body: UploadRequest, user_id: UserId, service: Photos
) -> UploadResponse:
    """A presigned PUT URL for one photo of the update; repeating the request issues a new URL."""
    upload = service.request_upload(update_id, user_id, body.kind)
    return UploadResponse(
        kind=upload.kind, upload_url=upload.url, expires_in_seconds=upload.expires_in_seconds
    )


@router.get("")
def list_photos(update_id: uuid.UUID, user_id: UserId, service: Photos) -> PhotosResponse:
    """The photos of the update with presigned GET URLs and the required kinds still missing."""
    return PhotosResponse.of(service.list_photos(update_id, user_id))
