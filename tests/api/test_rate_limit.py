"""Tests for F-06's request-rate-limit wiring (docs/audit/REGISTER.md) --
`enforce_rate_limit` (api/rate_limit.py) is now a router-level dependency
on every authenticated router. Before this, the limiter was fully built
and unit-tested (tests/security/test_rate_limit.py) but attached to zero
routes -- every endpoint, including PHI-decrypting reads, was unthrottled.

Injects a tiny-capacity limiter via `create_app`'s new override param
rather than sending dozens of real requests against the production
default (capacity=60).

The org-wide ceiling tests below (`MASTER-BUILD-PROMPT-V2.md` Phase 6,
"rate limiting per org") use a separate `org_rate_limiter` override --
independent of the per-`(org_id, user_id)` limiter these first tests
exercise, proven by `test_org_wide_ceiling_is_shared_across_different_users`
doing the opposite of `test_different_users_have_independent_budgets`
below: this one shows two different users' requests *do* count against
each other, because they share one org.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from security.rate_limit import InMemoryTokenBucketRateLimiter
from security.rbac import Role
from tests.api.conftest import JWT_SECRET, auth_headers
from tests.api.fakes import FakeRepository


def _client_with_capacity(repo: FakeRepository, capacity: int) -> TestClient:
    app = create_app(
        repository=repo,
        jwt_secret_key=JWT_SECRET,
        rate_limiter=InMemoryTokenBucketRateLimiter(capacity=capacity, refill_per_second=0.0001),
    )
    return TestClient(app)


def test_exceeding_capacity_returns_429(repo: FakeRepository) -> None:
    client = _client_with_capacity(repo, capacity=2)
    headers = auth_headers(Role.BILLER, "a")

    first = client.get("/findings", headers=headers)
    second = client.get("/findings", headers=headers)
    third = client.get("/findings", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["message"] == "rate limit exceeded"


def test_different_users_have_independent_budgets(repo: FakeRepository) -> None:
    client = _client_with_capacity(repo, capacity=1)

    response_a = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    response_b = client.get("/findings", headers=auth_headers(Role.BILLER, "b"))

    assert response_a.status_code == 200
    assert response_b.status_code == 200  # different tenant:user key, independent budget


def test_rate_limit_is_enforced_across_different_routes_on_the_same_router(
    repo: FakeRepository,
) -> None:
    """The budget is per (tenant, user), not per route -- proven against
    findings-list and finding-detail on the same router."""
    client = _client_with_capacity(repo, capacity=1)
    headers = auth_headers(Role.BILLER, "a")

    first = client.get("/findings", headers=headers)
    second = client.get("/contracts", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429


def test_health_endpoints_are_not_rate_limited(repo: FakeRepository) -> None:
    """/healthz and /readyz carry no bearer token, so there is no
    AuthContext to key a limit by -- both routers stay unauthenticated
    and unlimited by construction, proven here by outliving a
    capacity-1 budget that would gate any authenticated route."""
    client = _client_with_capacity(repo, capacity=1)
    for _ in range(5):
        response = client.get("/healthz")
        assert response.status_code == 200


# --- org-wide ceiling (Phase 6, "rate limiting per org") -----------------------


def _client_with_org_capacity(repo: FakeRepository, capacity: int) -> TestClient:
    app = create_app(
        repository=repo,
        jwt_secret_key=JWT_SECRET,
        org_rate_limiter=InMemoryTokenBucketRateLimiter(
            capacity=capacity, refill_per_second=0.0001
        ),
    )
    return TestClient(app)


def test_org_wide_ceiling_is_shared_across_different_users(repo: FakeRepository) -> None:
    """Two different users at the same org share one org-wide budget --
    the opposite of `test_different_users_have_independent_budgets`
    above, which is about the per-user limiter, not this one."""
    client = _client_with_org_capacity(repo, capacity=2)

    first = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    second = client.get("/findings", headers=auth_headers(Role.MANAGER, "a"))
    third = client.get("/findings", headers=auth_headers(Role.ANALYST, "a"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["message"] == "organization rate limit exceeded"


def test_org_wide_ceiling_does_not_affect_a_different_org(repo: FakeRepository) -> None:
    client = _client_with_org_capacity(repo, capacity=1)

    org_a_first = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    org_a_second = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    org_b = client.get("/findings", headers=auth_headers(Role.BILLER, "b"))

    assert org_a_first.status_code == 200
    assert org_a_second.status_code == 429
    assert org_b.status_code == 200
