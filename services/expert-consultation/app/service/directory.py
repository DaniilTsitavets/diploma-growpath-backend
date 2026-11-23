import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.service.errors import Conflict, InvalidRequest, NotFound, Unavailable


class Role(StrEnum):
    USER = "user"
    EXPERT = "expert"
    ADMIN = "admin"


# The identity provider keeps roles as groups; most privileged first.
ROLE_GROUPS: dict[Role, str] = {
    Role.ADMIN: "admins",
    Role.EXPERT: "experts",
    Role.USER: "users",
}


def role_of(groups: frozenset[str] | set[str]) -> Role | None:
    """The most privileged role among the groups, None when there is no role group."""
    return next((role for role, group in ROLE_GROUPS.items() if group in groups), None)


@dataclass(frozen=True)
class DirectoryUser:
    user_id: uuid.UUID
    username: str
    email: str | None
    groups: frozenset[str]
    enabled: bool
    created_at: datetime

    @property
    def role(self) -> Role | None:
        return role_of(self.groups)


@dataclass(frozen=True)
class DirectoryPage:
    users: list[DirectoryUser]
    next_token: str | None


class UserNotFound(NotFound):
    code = "user-not-found"

    def __init__(self) -> None:
        super().__init__("the user does not exist")


class UserExists(Conflict):
    code = "user-exists"

    def __init__(self) -> None:
        super().__init__("a user with this email already exists")


class CannotModifySelf(Conflict):
    code = "cannot-modify-self"

    def __init__(self) -> None:
        super().__init__("an admin cannot change their own role or disable themselves")


class InvalidPassword(InvalidRequest):
    code = "invalid-password"

    def __init__(self) -> None:
        super().__init__("the password does not meet the password policy")


class DirectoryUnavailable(Unavailable):
    def __init__(self) -> None:
        super().__init__("the user directory did not answer")


class UserDirectory(Protocol):
    """The identity provider's user store. Raises the errors above."""

    def list_users(self, limit: int, page_token: str | None) -> DirectoryPage:
        """Up to `limit` users with their groups; `next_token` is None on the last page."""
        ...

    def get_user(self, user_id: uuid.UUID) -> DirectoryUser: ...

    def create_user(self, email: str, password: str, role: Role) -> DirectoryUser:
        """A confirmed user with a permanent password and exactly the group of `role`."""
        ...

    def set_role(self, user_id: uuid.UUID, role: Role) -> None:
        """Make `role`'s group the only role group of the user."""
        ...

    def set_enabled(self, user_id: uuid.UUID, enabled: bool) -> None: ...

    def set_password(self, user_id: uuid.UUID, password: str) -> None:
        """Replace the password with a permanent one."""
        ...
