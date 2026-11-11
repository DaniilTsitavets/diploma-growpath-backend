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


class AiQuotaUnavailable(Unavailable):
    """The quota service could not give an answer; callers decide whether to degrade."""

    def __init__(self) -> None:
        super().__init__("the quota service did not answer")


class ExpertQuotaExhausted(Conflict):
    code = "expert-quota-exhausted"

    def __init__(self) -> None:
        super().__init__("the expert quota of the current period is used up")


class ExpertQuotaUnavailable(Unavailable):
    """The quota service could not give an answer about the expert quota."""

    def __init__(self) -> None:
        super().__init__("the quota service did not answer")


class ExpertReservationCommitted(Exception):
    """Entitlement refused to release the reservation: a ticket already paid for it."""


class EscalationNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the update has no escalation")


class EscalationNotOffered(Conflict):
    code = "escalation-not-offered"

    def __init__(self) -> None:
        super().__init__("no expert is on offer for this update; describe the question instead")


class DiagnosisNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the update has no diagnosis")


class PhotosMissing(InvalidRequest):
    def __init__(self, kinds: list[str]) -> None:
        super().__init__(f"photos required for a diagnosis are missing: {', '.join(kinds)}")


class DiagnosisConflict(Conflict):
    """The diagnosis is in a state that does not allow the request."""

    code = "diagnosis-conflict"


class RootsPhotoNotRequested(Conflict):
    code = "roots-photo-not-requested"

    def __init__(self) -> None:
        super().__init__("a roots photo is accepted only after the AI asked for one")


class ReservationUnusable(Exception):
    """The reservation is released, expired or unknown to entitlement: a new id is needed."""


class PublishFailed(Exception):
    """The event broker did not accept the event."""


class ContextTooLarge(Exception):
    """The escalation context exceeds the limit even without the journal history."""


class InvalidCursor(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("the cursor is not valid")
