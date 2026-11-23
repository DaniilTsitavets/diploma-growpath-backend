import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from growpath_observability import CORRELATION_HEADER, get_correlation_id
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import Problem
from app.service.errors import (
    CatalogUnavailable,
    IdempotencyKeyReused,
    InvalidCursor,
    ItemNotFound,
    ItemSlugNotFound,
    PaymentFailed,
)

PROBLEM_JSON = "application/problem+json"
DEFAULT_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    422: "validation-error",
    500: "internal-error",
    503: "service-unavailable",
}
RESPONSE_DESCRIPTIONS = {
    401: "The X-User-Id header is missing or malformed",
    402: "Payment was declined",
    403: "The role of the caller cannot use this endpoint",
    404: "The resource does not exist",
    422: "The request does not match the contract",
    503: "A dependency is unreachable",
}
logger = logging.getLogger(__name__)


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` entries for the given problem statuses."""
    return {
        status: {"model": Problem, "description": RESPONSE_DESCRIPTIONS[status]}
        for status in statuses
    }


def default_code(status: int) -> str:
    return DEFAULT_CODES.get(status) or HTTPStatus(status).phrase.lower().replace(" ", "-")


def problem_response(
    status: int,
    detail: str | None = None,
    *,
    code: str | None = None,
    title: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: object,
) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": title or HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "correlation_id": get_correlation_id(),
        "code": code or default_code(status),
        **extra,
    }
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return problem_response(exc.status_code, str(exc.detail))


async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return problem_response(
        422,
        "Request does not match the contract",
        errors=jsonable_encoder(exc.errors()),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc, extra={"path": request.url.path})
    response = problem_response(500)
    if correlation_id := get_correlation_id():
        # ServerErrorMiddleware sends this response outside the correlation middleware.
        response.headers[CORRELATION_HEADER] = correlation_id
    return response


def domain_error_handler(status: int, code: str, detail: str | None = None):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return problem_response(status, detail or str(exc), code=code)

    return handle


def unavailable_handler(retry_after_seconds: int):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return problem_response(
            503,
            "The catalog is temporarily unavailable, retry shortly",
            code="catalog-unavailable",
            headers={"Retry-After": str(retry_after_seconds)},
        )

    return handle


async def handle_item_slug_not_found(_: Request, exc: Exception) -> JSONResponse:
    return problem_response(
        404, "No active item has this slug", code="item-not-found", title="Item not found"
    )


def register_error_handlers(app: FastAPI, retry_after_seconds: int) -> None:
    app.add_exception_handler(ItemNotFound, domain_error_handler(404, "item-not-found"))
    app.add_exception_handler(ItemSlugNotFound, handle_item_slug_not_found)
    app.add_exception_handler(CatalogUnavailable, unavailable_handler(retry_after_seconds))
    app.add_exception_handler(
        IdempotencyKeyReused,
        domain_error_handler(
            422, "idempotency-key-reused", "Idempotency-Key was already used for a different item"
        ),
    )
    app.add_exception_handler(
        InvalidCursor,
        domain_error_handler(
            422, "invalid-cursor", "The cursor is not valid, start from the first page"
        ),
    )
    app.add_exception_handler(
        PaymentFailed, domain_error_handler(402, "payment-declined", "Payment was declined")
    )
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
