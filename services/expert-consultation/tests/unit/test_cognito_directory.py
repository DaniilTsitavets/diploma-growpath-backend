import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import ANY, Stubber

from app.identity.cognito import CognitoUserDirectory, create_cognito_client
from app.service.cursor import InvalidCursor
from app.service.directory import (
    DirectoryUnavailable,
    InvalidPassword,
    Role,
    UserExists,
    UserNotFound,
)
from tests.unit.settings import USER_POOL_ID, make_settings

CREATED_AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
PASSWORD = "S3cret-pass-phrase"
USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def dummy_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@contextmanager
def directory_with_stubs() -> Iterator[tuple[CognitoUserDirectory, Stubber]]:
    client = create_cognito_client(make_settings())
    with Stubber(client) as stubber:
        yield CognitoUserDirectory(client, USER_POOL_ID), stubber
        stubber.assert_no_pending_responses()


def attributes(user_id: uuid.UUID, email: str = "a@example.com") -> list[dict[str, str]]:
    return [{"Name": "sub", "Value": str(user_id)}, {"Name": "email", "Value": email}]


def raw_user(user_id: uuid.UUID = USER_ID, **overrides) -> dict:
    return {
        "Username": str(user_id),
        "Attributes": attributes(user_id),
        "UserCreateDate": CREATED_AT,
        "Enabled": True,
        **overrides,
    }


def groups_response(*names: str) -> dict:
    return {"Groups": [{"GroupName": name} for name in names]}


def expect_groups(stubber: Stubber, user_id: uuid.UUID, *names: str) -> None:
    stubber.add_response(
        "admin_list_groups_for_user",
        groups_response(*names),
        {"UserPoolId": USER_POOL_ID, "Username": str(user_id)},
    )


def error(stubber: Stubber, operation: str, code: str) -> None:
    stubber.add_client_error(operation, service_error_code=code, http_status_code=400)


def test_a_user_is_created_confirmed_and_put_in_the_role_group():
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "admin_create_user",
            {"User": {"Username": "a@example.com", "Attributes": attributes(USER_ID)}},
            {
                "UserPoolId": USER_POOL_ID,
                "Username": "a@example.com",
                "UserAttributes": [
                    {"Name": "email", "Value": "a@example.com"},
                    {"Name": "email_verified", "Value": "true"},
                ],
                "TemporaryPassword": PASSWORD,
                "MessageAction": "SUPPRESS",
            },
        )
        stubber.add_response(
            "admin_set_user_password",
            {},
            {
                "UserPoolId": USER_POOL_ID,
                "Username": str(USER_ID),
                "Password": PASSWORD,
                "Permanent": True,
            },
        )
        stubber.add_response(
            "admin_add_user_to_group",
            {},
            {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID), "GroupName": "experts"},
        )

        user = directory.create_user("a@example.com", PASSWORD, Role.EXPERT)

    assert user.user_id == USER_ID and user.email == "a@example.com"
    assert user.groups == {"experts"} and user.role is Role.EXPERT


def test_an_existing_username_is_a_duplicate():
    with directory_with_stubs() as (directory, stubber):
        error(stubber, "admin_create_user", "UsernameExistsException")

        with pytest.raises(UserExists):
            directory.create_user("a@example.com", PASSWORD, Role.USER)


def test_a_rejected_password_is_invalid():
    with directory_with_stubs() as (directory, stubber):
        error(stubber, "admin_create_user", "InvalidPasswordException")

        with pytest.raises(InvalidPassword):
            directory.create_user("a@example.com", "weak", Role.USER)


def test_a_half_created_user_is_removed_when_a_later_step_fails():
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "admin_create_user",
            {"User": {"Username": "a@example.com", "Attributes": attributes(USER_ID)}},
        )
        error(stubber, "admin_set_user_password", "InvalidPasswordException")
        stubber.add_response(
            "admin_delete_user", {}, {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID)}
        )

        with pytest.raises(InvalidPassword):
            directory.create_user("a@example.com", PASSWORD, Role.USER)


def test_a_failed_cleanup_does_not_hide_the_original_error():
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "admin_create_user",
            {"User": {"Username": "a@example.com", "Attributes": attributes(USER_ID)}},
        )
        stubber.add_response("admin_set_user_password", {})
        error(stubber, "admin_add_user_to_group", "ResourceNotFoundException")
        error(stubber, "admin_delete_user", "InternalErrorException")

        with pytest.raises(DirectoryUnavailable):
            directory.create_user("a@example.com", PASSWORD, Role.USER)


def test_a_user_is_read_with_the_groups_and_state():
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "admin_get_user",
            {
                "Username": "a@example.com",
                "UserAttributes": attributes(USER_ID),
                "UserCreateDate": CREATED_AT,
                "Enabled": False,
            },
            {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID)},
        )
        expect_groups(stubber, USER_ID, "users", "admins")

        user = directory.get_user(USER_ID)

    assert user.username == "a@example.com" and user.enabled is False
    assert user.created_at == CREATED_AT and user.role is Role.ADMIN


def test_an_unknown_user_is_not_found():
    with directory_with_stubs() as (directory, stubber):
        error(stubber, "admin_get_user", "UserNotFoundException")

        with pytest.raises(UserNotFound):
            directory.get_user(USER_ID)


def test_a_page_of_users_carries_the_pagination_token():
    other = uuid.uuid4()
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "list_users",
            {"Users": [raw_user(), raw_user(other)], "PaginationToken": "next"},
            {"UserPoolId": USER_POOL_ID, "Limit": 2, "PaginationToken": "prev"},
        )
        expect_groups(stubber, USER_ID, "users")
        expect_groups(stubber, other)

        page = directory.list_users(2, "prev")

    assert [user.user_id for user in page.users] == [USER_ID, other]
    assert [user.role for user in page.users] == [Role.USER, None]
    assert page.next_token == "next"


def test_a_page_larger_than_the_directory_limit_is_assembled_from_several_calls():
    users = [uuid.uuid4() for _ in range(62)]
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "list_users",
            {"Users": [raw_user(u) for u in users[:60]], "PaginationToken": "t1"},
            {"UserPoolId": USER_POOL_ID, "Limit": 60},
        )
        for user_id in users[:60]:
            expect_groups(stubber, user_id, "users")
        stubber.add_response(
            "list_users",
            {"Users": [raw_user(u) for u in users[60:]]},
            {"UserPoolId": USER_POOL_ID, "Limit": 2, "PaginationToken": "t1"},
        )
        for user_id in users[60:]:
            expect_groups(stubber, user_id, "users")

        page = directory.list_users(62, None)

    assert len(page.users) == 62 and page.next_token is None


def test_a_foreign_pagination_token_is_an_invalid_cursor():
    with directory_with_stubs() as (directory, stubber):
        error(stubber, "list_users", "InvalidParameterException")

        with pytest.raises(InvalidCursor):
            directory.list_users(10, "garbage")


def test_the_role_group_replaces_the_other_role_groups_and_keeps_foreign_ones():
    with directory_with_stubs() as (directory, stubber):
        expect_groups(stubber, USER_ID, "users", "beta-testers")
        stubber.add_response(
            "admin_remove_user_from_group",
            {},
            {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID), "GroupName": "users"},
        )
        stubber.add_response(
            "admin_add_user_to_group",
            {},
            {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID), "GroupName": "admins"},
        )

        directory.set_role(USER_ID, Role.ADMIN)


def test_a_user_who_already_has_the_role_is_left_alone():
    with directory_with_stubs() as (directory, stubber):
        expect_groups(stubber, USER_ID, "experts")

        directory.set_role(USER_ID, Role.EXPERT)


@pytest.mark.parametrize(
    ("enabled", "operation"), [(False, "admin_disable_user"), (True, "admin_enable_user")]
)
def test_a_user_is_disabled_or_enabled(enabled: bool, operation: str):
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(operation, {}, {"UserPoolId": USER_POOL_ID, "Username": str(USER_ID)})

        directory.set_enabled(USER_ID, enabled)


def test_a_password_is_set_as_permanent():
    with directory_with_stubs() as (directory, stubber):
        stubber.add_response(
            "admin_set_user_password",
            {},
            {
                "UserPoolId": USER_POOL_ID,
                "Username": str(USER_ID),
                "Password": ANY,
                "Permanent": True,
            },
        )

        directory.set_password(USER_ID, PASSWORD)


@pytest.mark.parametrize("code", ["TooManyRequestsException", "InternalErrorException"])
def test_throttling_and_provider_failures_make_the_directory_unavailable(code: str):
    with directory_with_stubs() as (directory, stubber):
        error(stubber, "admin_disable_user", code)

        with pytest.raises(DirectoryUnavailable):
            directory.set_enabled(USER_ID, False)


def test_a_network_failure_makes_the_directory_unavailable():
    class FailingClient:
        def admin_disable_user(self, **_):
            raise EndpointConnectionError(endpoint_url="http://cognito")

    with pytest.raises(DirectoryUnavailable):
        CognitoUserDirectory(FailingClient(), USER_POOL_ID).set_enabled(USER_ID, False)


def test_the_client_uses_the_cognito_endpoint_then_the_shared_one_and_the_timeouts():
    own = create_cognito_client(
        make_settings(
            cognito_endpoint_url="http://cognito.test", aws_endpoint_url="http://localstack:4566"
        )
    )
    shared = create_cognito_client(
        make_settings(aws_endpoint_url="http://localstack:4566", aws_read_timeout_seconds=9)
    )

    assert own.meta.endpoint_url == "http://cognito.test"
    assert shared.meta.endpoint_url == "http://localstack:4566"
    assert shared.meta.config.read_timeout == 9
