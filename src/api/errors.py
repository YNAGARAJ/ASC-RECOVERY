"""Structured error responses that never echo raw exception text --
CLAUDE.md rule 6 / Phase 6 prompt: "structured errors that never echo
PHI." Full detail is logged through a logger with
`security.redaction.PHIRedactionFilter` attached; the client only ever
sees a generic code + message + request id, never interpolated exception
text that might carry a patient identifier.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from security.redaction import PHIRedactionFilter

logger = logging.getLogger("api.errors")
logger.addFilter(PHIRedactionFilter())


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return str(request_id) if request_id is not None else str(uuid.uuid4())


def _error_body(code: str, message: str, request_id: str) -> dict[str, str]:
    return {"error": code, "message": message, "request_id": request_id}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
        request_id = _request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("request_error", str(exc.detail), request_id),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        # Full exception detail (which may reference request data) only
        # ever reaches this redaction-filtered logger, never the response.
        logger.error(
            "unhandled exception on %s",
            request.url.path,
            exc_info=exc,
            extra={"request_id": request_id},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "an unexpected error occurred", request_id),
        )
