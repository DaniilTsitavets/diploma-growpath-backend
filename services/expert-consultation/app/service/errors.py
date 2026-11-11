class NotFound(Exception):
    """The resource does not exist (HTTP 404)."""


class Conflict(Exception):
    """The request conflicts with the state of the resource (HTTP 409)."""

    code = "conflict"


class InvalidRequest(Exception):
    """The request is well formed but its content cannot be used (HTTP 422)."""


class Unavailable(Exception):
    """A dependency of the service did not answer (HTTP 503)."""
