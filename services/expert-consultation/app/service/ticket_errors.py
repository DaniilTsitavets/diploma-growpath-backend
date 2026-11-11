from app.service.errors import Conflict, NotFound


class PublishFailed(Exception):
    """The event broker did not accept the event."""


class TicketNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the ticket does not exist")


class TicketExpired(Conflict):
    code = "ticket-expired"

    def __init__(self) -> None:
        super().__init__("the ticket expired before it was answered")


class TicketAlreadyAnswered(Conflict):
    code = "ticket-already-answered"

    def __init__(self) -> None:
        super().__init__("the ticket was answered by another expert")
