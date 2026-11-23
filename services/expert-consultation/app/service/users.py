import logging
import uuid
from dataclasses import dataclass

from app.service.cursor import decode_page_token, encode_page_token
from app.service.directory import CannotModifySelf, DirectoryUser, Role, UserDirectory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserPage:
    items: list[DirectoryUser]
    next_cursor: str | None


class UserAdminService:
    """Account administration for admins; passwords never reach the logs."""

    def __init__(
        self, directory: UserDirectory, default_page_size: int, max_page_size: int
    ) -> None:
        self._directory = directory
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size

    def list_users(self, limit: int | None, cursor: str | None) -> UserPage:
        page_size = min(limit or self._default_page_size, self._max_page_size)
        token = decode_page_token(cursor) if cursor is not None else None
        page = self._directory.list_users(page_size, token)
        next_cursor = encode_page_token(page.next_token) if page.next_token else None
        return UserPage(page.users, next_cursor)

    def create_user(
        self, admin_id: uuid.UUID, email: str, password: str, role: Role
    ) -> DirectoryUser:
        user = self._directory.create_user(email, password, role)
        logger.info(
            "user created",
            extra={"admin_id": str(admin_id), "user_id": str(user.user_id), "role": role.value},
        )
        return user

    def change_role(self, admin_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> DirectoryUser:
        self._refuse_self(admin_id, user_id)
        self._directory.set_role(user_id, role)
        logger.info(
            "user role changed",
            extra={"admin_id": str(admin_id), "user_id": str(user_id), "role": role.value},
        )
        return self._directory.get_user(user_id)

    def set_enabled(self, admin_id: uuid.UUID, user_id: uuid.UUID, enabled: bool) -> None:
        self._refuse_self(admin_id, user_id)
        self._directory.set_enabled(user_id, enabled)
        logger.info(
            "user enabled state changed",
            extra={"admin_id": str(admin_id), "user_id": str(user_id), "enabled": enabled},
        )

    def reset_password(self, admin_id: uuid.UUID, user_id: uuid.UUID, password: str) -> None:
        self._directory.set_password(user_id, password)
        logger.info(
            "user password reset", extra={"admin_id": str(admin_id), "user_id": str(user_id)}
        )

    @staticmethod
    def _refuse_self(admin_id: uuid.UUID, user_id: uuid.UUID) -> None:
        if admin_id == user_id:
            raise CannotModifySelf
