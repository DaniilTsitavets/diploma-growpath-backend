import uuid
from dataclasses import replace
from datetime import UTC, datetime

from app.service.directory import (
    ROLE_GROUPS,
    DirectoryPage,
    DirectoryUnavailable,
    DirectoryUser,
    InvalidPassword,
    Role,
    UserExists,
    UserNotFound,
)

CREATED_AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
MIN_PASSWORD_LENGTH = 8


def make_user(role: Role | None = Role.USER, **overrides) -> DirectoryUser:
    user_id = overrides.pop("user_id", uuid.uuid4())
    fields = {
        "user_id": user_id,
        "username": f"{user_id}@example.com",
        "email": f"{user_id}@example.com",
        "groups": frozenset() if role is None else frozenset({ROLE_GROUPS[role]}),
        "enabled": True,
        "created_at": CREATED_AT,
    }
    return DirectoryUser(**{**fields, **overrides})


class FakeUserDirectory:
    def __init__(self) -> None:
        self.users: dict[uuid.UUID, DirectoryUser] = {}
        self.passwords: dict[uuid.UUID, str] = {}
        self.unavailable = False

    def add(self, user: DirectoryUser) -> DirectoryUser:
        self.users[user.user_id] = user
        return user

    def list_users(self, limit: int, page_token: str | None) -> DirectoryPage:
        self._check()
        ordered = list(self.users.values())
        start = int(page_token or 0)
        page = ordered[start : start + limit]
        more = start + limit < len(ordered)
        return DirectoryPage(page, str(start + limit) if more else None)

    def get_user(self, user_id: uuid.UUID) -> DirectoryUser:
        self._check()
        if user_id not in self.users:
            raise UserNotFound
        return self.users[user_id]

    def create_user(self, email: str, password: str, role: Role) -> DirectoryUser:
        self._check()
        if any(user.username == email for user in self.users.values()):
            raise UserExists
        self._check_password(password)
        user = make_user(role, username=email, email=email)
        self.passwords[user.user_id] = password
        return self.add(user)

    def set_role(self, user_id: uuid.UUID, role: Role) -> None:
        user = self.get_user(user_id)
        self.users[user_id] = replace(user, groups=frozenset({ROLE_GROUPS[role]}))

    def set_enabled(self, user_id: uuid.UUID, enabled: bool) -> None:
        user = self.get_user(user_id)
        self.users[user_id] = replace(user, enabled=enabled)

    def set_password(self, user_id: uuid.UUID, password: str) -> None:
        self.get_user(user_id)
        self._check_password(password)
        self.passwords[user_id] = password

    def _check(self) -> None:
        if self.unavailable:
            raise DirectoryUnavailable

    @staticmethod
    def _check_password(password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidPassword
