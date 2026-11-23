from typing import Any

PROBLEM_REF = {"$ref": "#/components/schemas/Problem"}

UNAUTHORIZED = "unauthorized"
NOT_FOUND = "not-found"
VALIDATION_ERROR = "validation-error"
SERVICE_UNAVAILABLE = "service-unavailable"

RETRY_AFTER = {
    "Retry-After": {"description": "Seconds to wait before retrying", "schema": {"type": "integer"}}
}


def problem(status: int, description: str, *codes: str, retry_after: bool = False) -> dict:
    """An OpenAPI response of problem+json whose `code` is one of `codes`."""
    schema: dict[str, Any] = PROBLEM_REF
    if codes:
        schema = {"allOf": [PROBLEM_REF, {"properties": {"code": {"enum": list(codes)}}}]}
    response: dict[str, Any] = {
        "description": description,
        "content": {"application/problem+json": {"schema": schema}},
    }
    if retry_after:
        response["headers"] = RETRY_AFTER
    return {status: response}


def identity_required() -> dict:
    return problem(401, "`X-User-Id` is missing or not a user id", UNAUTHORIZED)


def not_found(description: str) -> dict:
    return problem(404, description, NOT_FOUND)


def invalid_request(description: str, *codes: str) -> dict:
    return problem(422, description, VALIDATION_ERROR, *codes)


def unavailable(description: str) -> dict:
    return problem(503, description, SERVICE_UNAVAILABLE, retry_after=True)


def responses(*parts: dict) -> dict[int | str, dict[str, Any]]:
    merged: dict[int | str, dict[str, Any]] = {}
    for part in parts:
        merged.update(part)
    return merged
