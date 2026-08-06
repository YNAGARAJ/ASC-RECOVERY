"""HSTS response header (F-07, docs/audit/REGISTER.md) -- instructs a
browser to never downgrade to plain HTTP after the first HTTPS response,
closing the gap a bare `aws_lb_listener` HTTP->HTTPS redirect alone
doesn't cover on its own (a redirect only helps a client that already
attempted this host and got redirected; HSTS is what stops a client from
attempting plain HTTP again on a later visit in the first place).

Raw ASGI middleware, not `BaseHTTPMiddleware`, matching
`api.request_context.RequestIDMiddleware`'s established pattern in this
codebase -- see that module's docstring for why.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

_HSTS_HEADER = b"strict-transport-security"
# Two years, includeSubDomains -- the standard minimum for HSTS preload
# list submission. Submitting to the preload list itself is a manual,
# out-of-band step against a real live domain (hstspreload.org), not
# something Terraform or this middleware can do from here.
_HSTS_VALUE = b"max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HSTS_HEADER, _HSTS_VALUE))
            await send(message)

        await self.app(scope, receive, send_wrapper)
