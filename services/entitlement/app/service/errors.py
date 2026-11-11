class ReservationConflict(Exception):
    """The request conflicts with the state of the quota or the reservation (HTTP 409)."""

    code = "conflict"


class QuotaExhausted(ReservationConflict):
    code = "quota_exhausted"

    def __init__(self) -> None:
        super().__init__("the quota of the current period is used up")


class ReservationUnusable(ReservationConflict):
    code = "reservation_unusable"

    def __init__(self, status: str, expired: bool) -> None:
        reason = "expired" if expired else status
        super().__init__(f"the reservation is {reason}")
        self.status = status


class ReservationIdInUse(ReservationConflict):
    code = "reservation_id_in_use"

    def __init__(self) -> None:
        super().__init__("the reservation id is already in use")


class ReservationNotFound(Exception):
    """Missing, or owned by another user: the two are deliberately indistinguishable."""

    def __init__(self) -> None:
        super().__init__("the reservation does not exist")


class LockTimeout(Exception):
    """Another reservation of the same user held the quota lock for too long (HTTP 503)."""


class QueueUnavailable(Exception):
    """The message queue did not answer."""


class InvalidMessage(Exception):
    """A queue message that cannot be understood; retrying will not help."""
