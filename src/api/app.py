"""FastAPI app factory. Takes its `Repository` and JWT secret as explicit
arguments rather than constructing them internally, so tests can inject a
`FakeRepository` and production wiring (`src/main.py`, Phase 9) can
inject a real `PostgresRepository` without this module knowing the
difference -- same dependency-injection principle as every other
port/adapter in this codebase.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.errors import register_exception_handlers
from api.rate_limit import default_rate_limiter
from api.repository import Repository
from api.request_context import RequestIDMiddleware
from api.routes import (
    api_keys,
    audit,
    auth,
    contracts,
    findings,
    health,
    invitations,
    org_policy,
    organizations,
    packets,
    remittances,
)
from api.security_headers import SecurityHeadersMiddleware
from observability.alert_state import RollingWindowCounter
from observability.notifications import LoggingNotificationPort, NotificationPort
from security.rate_limit import AccountLockoutTracker, RateLimiter

# F-11 (docs/audit/REGISTER.md): a burst of not-found responses on a
# direct-id lookup within this long a window is the cross-tenant-probe
# signal -- see evaluate_cross_tenant_probe_alert's own docstring for why
# a 404 (never a distinguishable error) is the only observable proxy.
_NOT_FOUND_TRACKER_WINDOW_SECONDS = 300.0


def create_app(
    *,
    repository: Repository,
    jwt_secret_key: str,
    rate_limiter: RateLimiter | None = None,
    lockout_tracker: AccountLockoutTracker | None = None,
    notifier: NotificationPort | None = None,
    not_found_tracker: RollingWindowCounter | None = None,
) -> FastAPI:
    app = FastAPI(title="ASC Underpayment Recovery API", version="0.1.0")
    app.state.repository = repository
    app.state.jwt_secret_key = jwt_secret_key
    # All default to a fresh instance per create_app() call (never a
    # module-level singleton) -- see api/rate_limit.py's docstring for why
    # that matters for test isolation, not just production correctness.
    # Overridable so a test can inject a tiny-capacity/tiny-threshold
    # instance to exercise the 429/lockout/alert paths without sending
    # dozens of real requests, or a fake NotificationPort to assert on
    # dispatched alerts.
    app.state.rate_limiter = rate_limiter if rate_limiter is not None else default_rate_limiter()
    app.state.lockout_tracker = (
        lockout_tracker if lockout_tracker is not None else AccountLockoutTracker()
    )
    app.state.notifier = notifier if notifier is not None else LoggingNotificationPort()
    app.state.not_found_tracker = (
        not_found_tracker
        if not_found_tracker is not None
        else RollingWindowCounter(window_seconds=_NOT_FOUND_TRACKER_WINDOW_SECONDS)
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(remittances.router)
    app.include_router(findings.router)
    app.include_router(contracts.router)
    app.include_router(organizations.router)
    app.include_router(invitations.router)
    app.include_router(api_keys.router)
    app.include_router(org_policy.router)
    app.include_router(audit.router)
    app.include_router(packets.router)

    return app
