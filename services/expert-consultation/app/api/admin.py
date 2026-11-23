import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import current_admin_id, get_user_admin_service
from app.api.errors import problem_responses
from app.api.schemas import (
    CreateUserRequest,
    PasswordRequest,
    RoleRequest,
    UserResponse,
    UsersResponse,
)
from app.service.users import UserAdminService

router = APIRouter(prefix="/v1/admin/users", tags=["admin"])

AdminId = Annotated[uuid.UUID, Depends(current_admin_id)]
Users = Annotated[UserAdminService, Depends(get_user_admin_service)]


@router.get("", responses=problem_responses(401, 403, 422, 503))
def list_users(
    _: AdminId,
    service: Users,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> UsersResponse:
    """Users of the pool; pass `next_cursor` of a page as `cursor` for the next one.

    `role` is the most privileged of the user's groups, null when there is none. 401 without a
    valid `X-User-Id`, 403 outside the admins group, 422 `invalid-cursor` for a foreign cursor.
    """
    return UsersResponse.of(service.list_users(limit, cursor))


@router.post("", status_code=201, responses=problem_responses(401, 403, 409, 422, 503))
def create_user(body: CreateUserRequest, admin_id: AdminId, service: Users) -> UserResponse:
    """Create a confirmed user with a permanent password in the group of `role`.

    409 `user-exists` for a known email, 422 `invalid-password` when the password policy rejects it.
    """
    user = service.create_user(admin_id, body.email, body.password.get_secret_value(), body.role)
    return UserResponse.of(user)


@router.put("/{user_id}/role", responses=problem_responses(401, 403, 404, 409, 422, 503))
def change_role(
    user_id: uuid.UUID, body: RoleRequest, admin_id: AdminId, service: Users
) -> UserResponse:
    """Replace the user's role. 409 `cannot-modify-self` when `user_id` is the caller."""
    return UserResponse.of(service.change_role(admin_id, user_id, body.role))


@router.post(
    "/{user_id}/disable", status_code=204, responses=problem_responses(401, 403, 404, 409, 422, 503)
)
def disable_user(user_id: uuid.UUID, admin_id: AdminId, service: Users) -> Response:
    """Block sign-in. 409 `cannot-modify-self` when `user_id` is the caller."""
    service.set_enabled(admin_id, user_id, False)
    return Response(status_code=204)


@router.post(
    "/{user_id}/enable", status_code=204, responses=problem_responses(401, 403, 404, 409, 422, 503)
)
def enable_user(user_id: uuid.UUID, admin_id: AdminId, service: Users) -> Response:
    """Allow sign-in again. 409 `cannot-modify-self` when `user_id` is the caller."""
    service.set_enabled(admin_id, user_id, True)
    return Response(status_code=204)


@router.post(
    "/{user_id}/password", status_code=204, responses=problem_responses(401, 403, 404, 422, 503)
)
def reset_password(
    user_id: uuid.UUID, body: PasswordRequest, admin_id: AdminId, service: Users
) -> Response:
    """Set a new permanent password. 422 `invalid-password` when the password policy rejects it."""
    service.reset_password(admin_id, user_id, body.password.get_secret_value())
    return Response(status_code=204)
