from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json; charset=utf-8"


class DomainError(Exception):
    """Business error mapped to an RFC 7807 problem response."""

    status_code = 400
    title = "Bad request"

    def __init__(self, detail: str, *, title: str | None = None, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if title:
            self.title = title
        if status_code:
            self.status_code = status_code


class NotFoundError(DomainError):
    status_code = 404
    title = "Not found"


class ConflictError(DomainError):
    status_code = 409
    title = "Conflict"


class UnauthorizedError(DomainError):
    status_code = 401
    title = "Unauthorized"


class ForbiddenError(DomainError):
    status_code = 403
    title = "Forbidden"


class UnprocessableError(DomainError):
    status_code = 422
    title = "Unprocessable input"


def _problem(request: Request, status: int, title: str, detail: object) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "request_id": getattr(request.state, "request_id", None),
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        resp = _problem(request, exc.status_code, exc.title, exc.detail)
        if exc.status_code == 401:
            resp.headers["WWW-Authenticate"] = "Bearer"
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, "HTTP error", exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return _problem(request, 422, "Validation error", errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=str(request.url.path))
        return _problem(request, 500, "Internal server error", "An unexpected error occurred.")
