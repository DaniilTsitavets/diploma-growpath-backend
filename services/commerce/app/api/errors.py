import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from growpath_observability import get_correlation_id
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger(__name__)


def problem_response(status: int, title: str, detail: str | None = None, **extra) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": title,
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
        422, "Validation failed", "Request does not match the contract", errors=exc.errors()
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc, extra={"path": request.url.path})
    return problem_response(500, "Internal server error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
