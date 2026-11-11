import logging
import uuid

import pytest

from app.service.cursor import InvalidCursor
from app.service.expert_tickets import ExpertTicketService
from app.service.photos import PhotoLink, PhotoStorageUnavailable, photo_refs
from app.service.ticket_errors import TicketNotFound
from tests.unit.fake_storage import FakePhotoStorage
from tests.unit.fakes import HOUR, NOW, FakeUnitOfWork, make_ticket

DEFAULT_PAGE = 2
MAX_PAGE = 3


class Harness:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.storage = FakePhotoStorage()
        self.service = ExpertTicketService(lambda: self.uow, self.storage, DEFAULT_PAGE, MAX_PAGE)

    def open_ticket(self, hours_left: int, **overrides):
        fields = {"expires_at": NOW + hours_left * HOUR, **overrides}
        return self.uow.tickets.add(make_ticket(**fields))


@pytest.fixture
def harness() -> Harness:
    return Harness()


def test_the_queue_lists_open_tickets_soonest_deadline_first(harness: Harness):
    later = harness.open_ticket(10)
    sooner = harness.open_ticket(2)

    page = harness.service.queue(None, None)

    assert [item.id for item in page.items] == [sooner.id, later.id]
    assert page.next_cursor is None


def test_a_full_page_hands_out_a_cursor_that_continues_where_it_ended(harness: Harness):
    tickets = [harness.open_ticket(hours) for hours in (1, 2, 3, 4, 5)]

    first = harness.service.queue(None, None)
    second = harness.service.queue(None, first.next_cursor)
    third = harness.service.queue(None, second.next_cursor)

    assert [i.id for i in first.items] == [t.id for t in tickets[:2]]
    assert [i.id for i in second.items] == [t.id for t in tickets[2:4]]
    assert [i.id for i in third.items] == [tickets[4].id]
    assert third.next_cursor is None


def test_the_page_size_follows_the_request_up_to_the_maximum(harness: Harness):
    for hours in range(1, 6):
        harness.open_ticket(hours)

    assert len(harness.service.queue(1, None).items) == 1
    assert len(harness.service.queue(50, None).items) == MAX_PAGE


def test_a_page_of_exactly_the_remaining_tickets_has_no_cursor(harness: Harness):
    harness.open_ticket(1)
    harness.open_ticket(2)

    assert harness.service.queue(2, None).next_cursor is None


def test_a_malformed_cursor_is_refused(harness: Harness):
    with pytest.raises(InvalidCursor):
        harness.service.queue(None, "garbage")


def test_a_summary_names_the_species_from_the_snapshot(harness: Harness):
    harness.open_ticket(5, context={"schema_version": 1, "species": "Mint"})

    (item,) = harness.service.queue(None, None).items

    assert item.species == "Mint"


def test_a_ticket_is_shown_with_a_presigned_url_per_photo(harness: Harness):
    ticket = harness.open_ticket(
        5,
        context={
            "schema_version": 1,
            "photos": [
                {"kind": "overview", "storage_key": "journals/j/updates/u/overview"},
                {"kind": "soil", "storage_key": "journals/j/updates/u/soil"},
            ],
        },
    )

    view = harness.service.view(ticket.id)

    assert view.ticket == ticket
    assert view.photos == [
        PhotoLink("overview", "http://storage.test/journals/j/updates/u/overview?method=get"),
        PhotoLink("soil", "http://storage.test/journals/j/updates/u/soil?method=get"),
    ]


def test_a_ticket_without_photos_has_no_links_and_signs_nothing(harness: Harness):
    ticket = harness.open_ticket(5, context={"schema_version": 1})

    assert harness.service.view(ticket.id).photos == []
    assert harness.storage.issued == []


def test_urls_are_issued_on_every_read_and_not_stored_in_the_context(harness: Harness):
    context = {"schema_version": 1, "photos": [{"kind": "overview", "storage_key": "k"}]}
    ticket = harness.open_ticket(5, context=context)

    harness.service.view(ticket.id)
    harness.service.view(ticket.id)

    assert harness.storage.issued == ["k", "k"]
    assert harness.uow.tickets.rows[ticket.id].context == context


def test_an_unknown_ticket_is_not_found(harness: Harness):
    with pytest.raises(TicketNotFound):
        harness.service.view(uuid.uuid4())


def test_a_storage_outage_fails_the_read(harness: Harness):
    ticket = harness.open_ticket(
        5, context={"schema_version": 1, "photos": [{"kind": "overview", "storage_key": "k"}]}
    )
    harness.storage.unavailable = True

    with pytest.raises(PhotoStorageUnavailable):
        harness.service.view(ticket.id)


def test_a_photo_entry_without_a_key_is_skipped_with_a_warning(caplog: pytest.LogCaptureFixture):
    context = {
        "photos": [
            {"kind": "overview"},
            "stray",
            {"kind": "soil", "storage_key": "k"},
            {"storage_key": "x"},
        ]
    }

    with caplog.at_level(logging.WARNING, logger="app.service.photos"):
        refs = photo_refs(context)

    assert [(ref.kind, ref.storage_key) for ref in refs] == [("soil", "k"), ("", "x")]
    assert len(caplog.records) == 2


@pytest.mark.parametrize("context", [{}, {"photos": None}, {"photos": []}])
def test_a_context_without_photos_has_no_refs(context: dict):
    assert photo_refs(context) == []
