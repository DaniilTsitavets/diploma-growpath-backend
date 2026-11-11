import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from growpath_observability import CORRELATION_HEADER, get_correlation_id
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.service.errors import Conflict, InvalidRequest, NotFound, Unavailable

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger(__name__)


def problem_response(
    status: int,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: object,
) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "correlation_id": get_correlation_id(),
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


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


async def handle_not_found(_: Request, exc: Exception) -> JSONResponse:
    return problem_response(404, str(exc))


async def handle_conflict(_: Request, exc: Exception) -> JSONResponse:
    return problem_response(409, str(exc), code=getattr(exc, "code", Conflict.code))


async def handle_invalid_request(_: Request, exc: Exception) -> JSONResponse:
    return problem_response(422, str(exc))


def unavailable_handler(retry_after_seconds: int | None):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("dependency unavailable", exc_info=exc, extra={"path": request.url.path})
        headers = None if retry_after_seconds is None else {"Retry-After": str(retry_after_seconds)}
        return problem_response(
            503, "A dependency of the service is unavailable, retry shortly", headers=headers
        )

    return handle


def register_error_handlers(app: FastAPI, retry_after_seconds: int | None = None) -> None:
    app.add_exception_handler(NotFound, handle_not_found)
    app.add_exception_handler(Conflict, handle_conflict)
    app.add_exception_handler(InvalidRequest, handle_invalid_request)
    app.add_exception_handler(Unavailable, unavailable_handler(retry_after_seconds))
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
