import logging
import uuid

import pytest

from app.service.cursor import InvalidCursor
from app.service.directory import (
    CannotModifySelf,
    DirectoryUnavailable,
    InvalidPassword,
    Role,
    UserExists,
    UserNotFound,
    role_of,
)
from app.service.users import UserAdminService
from tests.unit.fake_directory import FakeUserDirectory, make_user

ADMIN = uuid.uuid4()
PASSWORD = "S3cret-pass-phrase"


@pytest.fixture
def directory() -> FakeUserDirectory:
    return FakeUserDirectory()


@pytest.fixture
def service(directory: FakeUserDirectory) -> UserAdminService:
    return UserAdminService(directory, default_page_size=2, max_page_size=3)


def test_a_user_is_created_with_the_requested_role(
    service: UserAdminService, directory: FakeUserDirectory
):
    user = service.create_user(ADMIN, "new@example.com", PASSWORD, Role.EXPERT)

    assert user.role is Role.EXPERT and user.email == "new@example.com"
    assert directory.passwords[user.user_id] == PASSWORD


def test_a_duplicate_email_is_refused(service: UserAdminService):
    service.create_user(ADMIN, "new@example.com", PASSWORD, Role.USER)

    with pytest.raises(UserExists):
        service.create_user(ADMIN, "new@example.com", PASSWORD, Role.USER)


def test_a_weak_password_is_refused_and_nothing_is_stored(
    service: UserAdminService, directory: FakeUserDirectory
):
    with pytest.raises(InvalidPassword):
        service.create_user(ADMIN, "new@example.com", "short", Role.USER)

    assert directory.users == {}


def test_the_role_is_replaced(service: UserAdminService, directory: FakeUserDirectory):
    user = directory.add(make_user(Role.USER))

    changed = service.change_role(ADMIN, user.user_id, Role.EXPERT)

    assert changed.role is Role.EXPERT and changed.groups == {"experts"}


def test_an_admin_cannot_change_their_own_role(
    service: UserAdminService, directory: FakeUserDirectory
):
    directory.add(make_user(Role.ADMIN, user_id=ADMIN))

    with pytest.raises(CannotModifySelf):
        service.change_role(ADMIN, ADMIN, Role.USER)

    assert directory.users[ADMIN].role is Role.ADMIN


def test_an_unknown_user_is_not_found(service: UserAdminService):
    with pytest.raises(UserNotFound):
        service.change_role(ADMIN, uuid.uuid4(), Role.USER)


def test_a_user_is_disabled_and_enabled_again(
    service: UserAdminService, directory: FakeUserDirectory
):
    user = directory.add(make_user())

    service.set_enabled(ADMIN, user.user_id, False)
    assert directory.users[user.user_id].enabled is False

    service.set_enabled(ADMIN, user.user_id, True)
    assert directory.users[user.user_id].enabled is True


@pytest.mark.parametrize("enabled", [False, True])
def test_an_admin_cannot_disable_or_enable_themselves(service: UserAdminService, enabled: bool):
    with pytest.raises(CannotModifySelf):
        service.set_enabled(ADMIN, ADMIN, enabled)


def test_a_password_is_reset(service: UserAdminService, directory: FakeUserDirectory):
    user = directory.add(make_user())

    service.reset_password(ADMIN, user.user_id, PASSWORD)

    assert directory.passwords[user.user_id] == PASSWORD


def test_a_weak_reset_password_is_refused(service: UserAdminService, directory: FakeUserDirectory):
    user = directory.add(make_user())

    with pytest.raises(InvalidPassword):
        service.reset_password(ADMIN, user.user_id, "short")


def test_the_password_never_reaches_the_logs(
    service: UserAdminService, directory: FakeUserDirectory, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.DEBUG)
    user = service.create_user(ADMIN, "new@example.com", PASSWORD, Role.USER)

    service.reset_password(ADMIN, user.user_id, PASSWORD)

    assert caplog.records
    assert PASSWORD not in caplog.text
    assert PASSWORD not in repr([record.__dict__ for record in caplog.records])


def test_users_are_listed_page_by_page(service: UserAdminService, directory: FakeUserDirectory):
    for _ in range(3):
        directory.add(make_user())

    first = service.list_users(None, None)
    second = service.list_users(None, first.next_cursor)

    assert len(first.items) == 2 and first.next_cursor
    assert len(second.items) == 1 and second.next_cursor is None
    assert {user.user_id for user in first.items + second.items} == set(directory.users)


def test_the_page_size_is_capped(service: UserAdminService, directory: FakeUserDirectory):
    for _ in range(5):
        directory.add(make_user())

    assert len(service.list_users(50, None).items) == 3


def test_the_cursor_wraps_the_directory_token(
    service: UserAdminService, directory: FakeUserDirectory
):
    for _ in range(3):
        directory.add(make_user())

    cursor = service.list_users(None, None).next_cursor

    assert cursor and cursor != "2"


def test_a_cursor_that_is_not_ours_is_refused(service: UserAdminService):
    with pytest.raises(InvalidCursor):
        service.list_users(None, "%%%")


def test_an_unavailable_directory_surfaces(service: UserAdminService, directory: FakeUserDirectory):
    directory.unavailable = True

    with pytest.raises(DirectoryUnavailable):
        service.list_users(None, None)


@pytest.mark.parametrize(
    ("groups", "role"),
    [
        ({"users"}, Role.USER),
        ({"experts"}, Role.EXPERT),
        ({"users", "experts", "admins"}, Role.ADMIN),
        ({"users", "experts"}, Role.EXPERT),
        ({"other"}, None),
        (set(), None),
    ],
)
def test_the_role_is_the_most_privileged_group(groups: set[str], role: Role | None):
    assert role_of(groups) is role
