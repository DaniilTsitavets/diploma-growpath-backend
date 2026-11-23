from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UpdatePhotoModel
from app.repository.photos import SqlAlchemyPhotoRepository
from app.service.model import PhotoKind
from tests.db.factories import add_update


def test_a_photo_kind_is_stored_once_and_keeps_its_first_key(session: Session):
    repository = SqlAlchemyPhotoRepository(session)
    update_id = add_update(session)["id"]

    first = repository.add(update_id, PhotoKind.OVERVIEW, "first")
    again = repository.add(update_id, PhotoKind.OVERVIEW, "second")

    assert (first, again) == (True, False)
    assert session.scalars(select(UpdatePhotoModel.storage_key)).all() == ["first"]


def test_photos_of_an_update_are_listed_with_typed_kinds(session: Session):
    repository = SqlAlchemyPhotoRepository(session)
    update_id = add_update(session)["id"]
    other_id = add_update(session)["id"]
    repository.add(update_id, PhotoKind.SYMPTOM_CLOSEUP, "closeup-key")
    repository.add(update_id, PhotoKind.OVERVIEW, "overview-key")
    repository.add(other_id, PhotoKind.OVERVIEW, "other-key")

    photos = repository.list_for_update(update_id)

    assert {(photo.kind, photo.storage_key) for photo in photos} == {
        (PhotoKind.SYMPTOM_CLOSEUP, "closeup-key"),
        (PhotoKind.OVERVIEW, "overview-key"),
    }


def test_an_update_without_photos_has_an_empty_list(session: Session):
    assert SqlAlchemyPhotoRepository(session).list_for_update(add_update(session)["id"]) == []


def test_removing_a_photo_drops_only_that_kind_of_that_update(session: Session):
    repository = SqlAlchemyPhotoRepository(session)
    update_id = add_update(session)["id"]
    other_id = add_update(session)["id"]
    repository.add(update_id, PhotoKind.SYMPTOM_CLOSEUP, "closeup-key")
    repository.add(update_id, PhotoKind.OVERVIEW, "overview-key")
    repository.add(other_id, PhotoKind.SYMPTOM_CLOSEUP, "other-key")

    repository.remove(update_id, PhotoKind.SYMPTOM_CLOSEUP)
    repository.remove(update_id, PhotoKind.SYMPTOM_CLOSEUP)

    assert session.scalars(select(UpdatePhotoModel.storage_key)).all() != []
    assert {p.storage_key for p in repository.list_for_update(update_id)} == {"overview-key"}
    assert [p.storage_key for p in repository.list_for_update(other_id)] == ["other-key"]
