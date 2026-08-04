"""Request-ID propagation: every request gets an id (from an incoming
`X-Request-ID` header if the caller supplied one, otherwise generated),
stashed on `request.state.request_id` so `api.auth.get_auth_context` and
`api.errors`'s exception handlers can read it, echoed back on the
response, and threaded into every audit_log entry (`audit_log.request_id`,
added in migration 0003) -- per the Phase 6 prompt's "request ID
propagated into every log line and audit entry."

Implemented as raw ASGI middleware, not `BaseHTTPMiddleware` --
`BaseHTTPMiddleware` runs *outside* FastAPI's exception-handler layer, so
an unhandled exception raised downstream re-propagates through it instead
of first becoming the 500 response `api.errors.register_exception_handlers`
produces (a well-known Starlette gotcha). Raw ASGI middleware sits at the
right layer for this to work.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("latin-1")

logger = logging.getLogger("api.request")


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", [])
                if key == _REQUEST_ID_HEADER_BYTES
            ),
            None,
        )
        request_id = incoming or str(uuid.uuid4())
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id
        logger.info(
            "request started",
            extra={
                "request_id": request_id,
                "path": scope.get("path"),
                "method": scope.get("method"),
            },
        )

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)
