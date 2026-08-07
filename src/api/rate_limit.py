"""Thin FastAPI dependency wrapping security.rate_limit's token-bucket
limiter -- single-process/in-memory, same scope note as Phase 4's
`InMemoryTokenBucketRateLimiter` (a Redis-backed adapter is needed before
running more than one API instance, not built here).

Two independent limiters, two independent purposes (`MASTER-BUILD-
PROMPT-V2.md` Phase 6's "rate limiting per org"): `rate_limiter` bounds
one `(org_id, user_id)` pair's own request rate (protects the service
from a single noisy caller); `org_rate_limiter` bounds the *combined*
rate of every user (and API key) authenticating as one org (protects
shared capacity from one large customer's aggregate traffic starving
everyone else -- the per-user limiter alone doesn't do this, since N
users each get their own full budget independent of each other). Both
must allow a request for it to proceed.

Both limiter instances live on `app.state` (built by `api.app.create_app`),
not module-level singletons -- a module-level limiter would persist
across every `TestClient`/`create_app()` call in a test session, so an
early test could exhaust another, unrelated test's budget purely from
run order. Same per-app-state convention `app.state.repository`/
`app.state.jwt_secret_key` already use.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from api.auth import AuthContext, get_auth_context
from security.rate_limit import InMemoryTokenBucketRateLimiter, RateLimiter

_DEFAULT_CAPACITY = 60
_DEFAULT_REFILL_PER_SECOND = 1.0

# Deliberately well above one user's own budget above -- otherwise a
# single active user alone would exhaust the org's shared ceiling, making
# this a confusing, redundant copy of the per-user check rather than an
# actual aggregate-traffic protection. Still bounded, unlike "no ceiling
# at all" (today's behavior before this control existed).
_DEFAULT_ORG_CAPACITY = 600
_DEFAULT_ORG_REFILL_PER_SECOND = 10.0


def default_rate_limiter() -> RateLimiter:
    return InMemoryTokenBucketRateLimiter(
        capacity=_DEFAULT_CAPACITY, refill_per_second=_DEFAULT_REFILL_PER_SECOND
    )


def default_org_rate_limiter() -> RateLimiter:
    return InMemoryTokenBucketRateLimiter(
        capacity=_DEFAULT_ORG_CAPACITY, refill_per_second=_DEFAULT_ORG_REFILL_PER_SECOND
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

    org_limiter: RateLimiter = request.app.state.org_rate_limiter
    org_key = f"org:{ctx.org_id}"
    if not org_limiter.allow(org_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="organization rate limit exceeded",
        )
    return ctx
