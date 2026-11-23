class NotFound(Exception):
    """The resource is missing, or belongs to another user: the two are indistinguishable."""

    title: str | None = None


class Conflict(Exception):
    """The request conflicts with the state of the resource (HTTP 409)."""

    code = "conflict"


class InvalidRequest(Exception):
    """The request is well formed but its content cannot be used (HTTP 422)."""

    code = "validation-error"


class Unavailable(Exception):
    """A dependency of the service did not answer (HTTP 503)."""


class PlantNotFound(NotFound):
    title = "Plant not found"

    def __init__(self) -> None:
        super().__init__("the plant does not exist")


class JournalNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the journal does not exist")


class PlantingDateInFuture(InvalidRequest):
    def __init__(self) -> None:
        super().__init__("the planting date cannot be in the future")


class PlantingAlreadyRecorded(Conflict):
    code = "planting-already-recorded"

    def __init__(self) -> None:
        super().__init__("the planting date is already recorded and cannot be changed")


class UpdateNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the update does not exist")


class ChatTurnNotFound(NotFound):
    def __init__(self) -> None:
        super().__init__("the chat turn does not exist")


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


class PhotoNotUploaded(Conflict):
    code = "photo-not-uploaded"

    def __init__(self) -> None:
        super().__init__("no object was uploaded for this photo")


class PhotoTooLarge(InvalidRequest):
    code = "photo-too-large"

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"a photo must be between 1 and {max_bytes} bytes")


class PhotoTypeNotAllowed(InvalidRequest):
    code = "photo-type-not-allowed"

    def __init__(self, allowed: tuple[str, ...]) -> None:
        super().__init__(f"a photo must be one of: {', '.join(allowed)}")


class ReservationUnusable(Exception):
    """The reservation is released, expired or unknown to entitlement: a new id is needed."""


class PublishFailed(Exception):
    """The event broker did not accept the event."""


class ContextTooLarge(Exception):
    """The escalation context exceeds the limit even without the journal history."""


class InvalidCursor(InvalidRequest):
    code = "invalid-cursor"

    def __init__(self) -> None:
        super().__init__("the cursor is not valid")
