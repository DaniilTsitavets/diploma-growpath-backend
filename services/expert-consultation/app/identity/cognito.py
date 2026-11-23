import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings
from app.service.cursor import InvalidCursor
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

logger = logging.getLogger(__name__)

# Cognito returns at most this many users per ListUsers call.
LIST_USERS_PAGE_LIMIT = 60
KNOWN_ERRORS: dict[str, type[Exception]] = {
    "UserNotFoundException": UserNotFound,
    "UsernameExistsException": UserExists,
    "InvalidPasswordException": InvalidPassword,
}


def create_cognito_client(settings: Settings):
    return boto3.client(
        "cognito-idp",
        region_name=settings.aws_region,
        endpoint_url=settings.cognito_endpoint_url or settings.aws_endpoint_url,
        config=Config(
            connect_timeout=settings.aws_connect_timeout_seconds,
            read_timeout=settings.aws_read_timeout_seconds,
            retries={"max_attempts": settings.aws_max_attempts, "mode": "standard"},
        ),
    )


class CognitoUserDirectory:
    def __init__(self, client, user_pool_id: str) -> None:
        self._client = client
        self._pool = user_pool_id

    def list_users(self, limit: int, page_token: str | None) -> DirectoryPage:
        users: list[DirectoryUser] = []
        token = page_token
        while True:
            params: dict[str, Any] = {"Limit": min(limit - len(users), LIST_USERS_PAGE_LIMIT)}
            if token:
                params["PaginationToken"] = token
            response = self._call(
                "list_users", {"InvalidParameterException": InvalidCursor}, **params
            )
            users += [self._user(raw, raw["Attributes"]) for raw in response["Users"]]
            token = response.get("PaginationToken")
            if token is None or len(users) >= limit:
                return DirectoryPage(users, token)

    def get_user(self, user_id: uuid.UUID) -> DirectoryUser:
        raw = self._call("admin_get_user", Username=str(user_id))
        return self._user(raw, raw["UserAttributes"])

    def create_user(self, email: str, password: str, role: Role) -> DirectoryUser:
        created = self._call(
            "admin_create_user",
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            TemporaryPassword=password,
            MessageAction="SUPPRESS",
        )["User"]
        user_id = _attributes(created["Attributes"])["sub"]
        try:
            self._call(
                "admin_set_user_password", Username=user_id, Password=password, Permanent=True
            )
            self._call("admin_add_user_to_group", Username=user_id, GroupName=ROLE_GROUPS[role])
        except Exception:
            self._discard(user_id)
            raise
        return self._user(created, created["Attributes"], groups=frozenset({ROLE_GROUPS[role]}))

    def set_role(self, user_id: uuid.UUID, role: Role) -> None:
        username = str(user_id)
        current = self._groups(username)
        target = ROLE_GROUPS[role]
        for group in (current & set(ROLE_GROUPS.values())) - {target}:
            self._call("admin_remove_user_from_group", Username=username, GroupName=group)
        if target not in current:
            self._call("admin_add_user_to_group", Username=username, GroupName=target)

    def set_enabled(self, user_id: uuid.UUID, enabled: bool) -> None:
        operation = "admin_enable_user" if enabled else "admin_disable_user"
        self._call(operation, Username=str(user_id))

    def set_password(self, user_id: uuid.UUID, password: str) -> None:
        self._call(
            "admin_set_user_password", Username=str(user_id), Password=password, Permanent=True
        )

    def _groups(self, username: str) -> frozenset[str]:
        response = self._call("admin_list_groups_for_user", Username=username)
        return frozenset(group["GroupName"] for group in response["Groups"])

    def _user(
        self,
        raw: dict[str, Any],
        attributes: list[dict[str, str]],
        groups: frozenset[str] | None = None,
    ) -> DirectoryUser:
        values = _attributes(attributes)
        user_id = values["sub"]
        return DirectoryUser(
            user_id=uuid.UUID(user_id),
            username=raw["Username"],
            email=values.get("email"),
            groups=self._groups(user_id) if groups is None else groups,
            enabled=raw.get("Enabled", True),
            created_at=raw.get("UserCreateDate", datetime.fromtimestamp(0, UTC)),
        )

    def _discard(self, user_id: str) -> None:
        try:
            self._call("admin_delete_user", Username=user_id)
        except Exception:
            logger.error("half-created user could not be removed", extra={"user_id": user_id})

    def _call(
        self,
        operation: str,
        extra_errors: dict[str, type[Exception]] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        errors = {**KNOWN_ERRORS, **(extra_errors or {})}
        try:
            return getattr(self._client, operation)(UserPoolId=self._pool, **params)
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            if code in errors:
                raise errors[code]() from error
            logger.warning("cognito call failed", extra={"operation": operation, "error": code})
            raise DirectoryUnavailable from error
        except BotoCoreError as error:
            logger.warning("cognito call failed", extra={"operation": operation})
            raise DirectoryUnavailable from error


def _attributes(attributes: list[dict[str, str]]) -> dict[str, str]:
    return {attribute["Name"]: attribute["Value"] for attribute in attributes}
