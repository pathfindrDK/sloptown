"""API error type and handler producing `{ error, message, ... }` bodies (see docs/internal-api.md)."""

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra: Any):
        self.status = status
        self.error = error
        self.message = message
        self.extra = extra
        self.headers: dict[str, str] | None = None


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        {"error": exc.error, "message": exc.message, **exc.extra},
        status_code=exc.status,
        headers=exc.headers,
    )


def not_found(what: str = "Ikke fundet") -> ApiError:
    return ApiError(404, "not_found", what)


def forbidden(message: str = "Du har ikke adgang") -> ApiError:
    return ApiError(403, "forbidden", message)


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    errors = getattr(exc, "errors", lambda: [])()
    return JSONResponse(
        {"error": "invalid_request", "message": "Ugyldig forespørgsel", "detail": jsonable_encoder(errors)},
        status_code=422,
    )


async def http_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    status = getattr(exc, "status_code", 500)
    codes = {404: "not_found", 405: "method_not_allowed"}
    return JSONResponse(
        {"error": codes.get(status, "http_error"), "message": str(getattr(exc, "detail", ""))},
        status_code=status,
        headers=getattr(exc, "headers", None),
    )


async def unreachable_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """A project container (agentd, OpenCode) or Miira didn't answer: tell the client to retry."""
    return JSONResponse(
        {"error": "upstream_unreachable", "message": "Vibe svarer ikke lige nu. Prøv igen om lidt."},
        status_code=503,
        headers={"Retry-After": "5"},
    )
