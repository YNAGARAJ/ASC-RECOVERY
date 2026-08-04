"""Thin FastAPI dependency wrapping security.rate_limit's token-bucket
limiter -- single-process/in-memory, same scope note as Phase 4's
`InMemoryTokenBucketRateLimiter` (a Redis-backed adapter is needed before
running more than one API instance, not built here)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from api.auth import AuthContext, get_auth_context
from security.rate_limit import InMemoryTokenBucketRateLimiter

_limiter = InMemoryTokenBucketRateLimiter(capacity=60, refill_per_second=1.0)


def enforce_rate_limit(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    key = f"{ctx.tenant_id}:{ctx.user_id}"
    if not _limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded"
        )
    return ctx
