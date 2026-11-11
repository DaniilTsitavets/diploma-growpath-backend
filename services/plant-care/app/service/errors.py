class NotFound(Exception):
    """The resource is missing, or belongs to another user: the two are indistinguishable."""


class Conflict(Exception):
    """The request conflicts with the state of the resource (HTTP 409)."""

    code = "conflict"


class InvalidRequest(Exception):
    """The request is well formed but its content cannot be used (HTTP 422)."""


class Unavailable(Exception):
    """A dependency of the service did not answer (HTTP 503)."""


class JournalNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the journal does not exist")


class UpdateNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the update does not exist")


class AiQuotaExhausted(Conflict):
    code = "ai-quota-exhausted"

    def __init__(self) -> None:
        super().__init__("the AI quota of the current period is used up")


class AiQuotaUnavailable(Exception):
    """The quota service could not give an answer; callers decide whether to degrade."""


class InvalidCursor(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("the cursor is not valid")
