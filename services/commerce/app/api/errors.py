import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from growpath_observability import CORRELATION_HEADER, get_correlation_id
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.service.errors import IdempotencyKeyReused, ItemNotFound, PaymentFailed

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger(__name__)


def problem_response(status: int, detail: str | None = None, **extra: object) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "correlation_id": get_correlation_id(),
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


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


def domain_error_handler(status: int, detail: str | None = None):
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        return problem_response(status, detail or str(exc))

    return handle


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ItemNotFound, domain_error_handler(404))
    app.add_exception_handler(
        IdempotencyKeyReused,
        domain_error_handler(422, "Idempotency-Key was already used for a different item"),
    )
    app.add_exception_handler(PaymentFailed, domain_error_handler(402, "Payment was declined"))
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
