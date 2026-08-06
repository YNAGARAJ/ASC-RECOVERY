"""Thin FastAPI dependency wrapping security.rate_limit's token-bucket
limiter -- single-process/in-memory, same scope note as Phase 4's
`InMemoryTokenBucketRateLimiter` (a Redis-backed adapter is needed before
running more than one API instance, not built here).

The limiter instance itself lives on `app.state.rate_limiter` (built by
`api.app.create_app`), not a module-level singleton -- a module-level
limiter would persist across every `TestClient`/`create_app()` call in a
test session, so an early test could exhaust another, unrelated test's
budget for the same (tenant, user) key purely from run order. Same
per-app-state convention `app.state.repository`/`app.state.jwt_secret_key`
already use.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from api.auth import AuthContext, get_auth_context
from security.rate_limit import InMemoryTokenBucketRateLimiter, RateLimiter

_DEFAULT_CAPACITY = 60
_DEFAULT_REFILL_PER_SECOND = 1.0


def default_rate_limiter() -> RateLimiter:
    return InMemoryTokenBucketRateLimiter(
        capacity=_DEFAULT_CAPACITY, refill_per_second=_DEFAULT_REFILL_PER_SECOND
    )


def enforce_rate_limit(
    request: Request, ctx: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    limiter: RateLimiter = request.app.state.rate_limiter
    key = f"{ctx.org_id}:{ctx.user_id}"
    if not limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded"
        )
    return ctx
