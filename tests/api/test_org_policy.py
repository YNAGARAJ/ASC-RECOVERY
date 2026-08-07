"""Phase 5 step 6 (`docs/MASTER-BUILD-PROMPT-V2.md`): per-org policy --
`session_timeout_seconds` (an `issue_session` access-token TTL override),
`mfa_required` (always `true`, never client-settable), and `ip_allowlist`
(enforced in `api/auth.py::get_auth_context` for every authenticated
request, JWT or API key alike, so it protects a stolen token used from an
unexpected network too, not just login).
"""

from __future__ import annotations

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient

from security.mfa import generate_enrollment_secret
from security.rbac import Action, Role, can
from tests.api.conftest import JWT_SECRET, TENANT_A, auth_headers
from tests.api.fakes import FakeRepository

_PASSWORD = "correct horse battery staple"


def _totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


@pytest.mark.parametrize("role", list(Role))
def test_get_org_policy_matrix(client: TestClient, role: Role) -> None:
    response = client.get("/org-policy", headers=auth_headers(role, "a"))
    if not can(role, Action.MANAGE_USERS):
        assert response.status_code == 403, (role, response.text)
        return
    assert response.status_code == 200, (role, response.text)


@pytest.mark.parametrize("role", list(Role))
def test_set_org_policy_matrix(client: TestClient, role: Role) -> None:
    response = client.put(
        "/org-policy",
        json={"session_timeout_seconds": 3600, "ip_allowlist": []},
        headers=auth_headers(role, "a"),
    )
    if not can(role, Action.MANAGE_USERS):
        assert response.status_code == 403, (role, response.text)
        return
    assert response.status_code == 200, (role, response.text)


def test_get_org_policy_returns_application_defaults_when_unconfigured(
    client: TestClient,
) -> None:
    response = client.get("/org-policy", headers=auth_headers(Role.ORG_ADMIN, "a"))
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_timeout_seconds": None,
        "mfa_required": True,
        "ip_allowlist": [],
        "data_residency_region": None,
        "updated_at": None,
    }


def test_set_org_policy_round_trips(client: TestClient) -> None:
    # ip_allowlist deliberately empty here -- a non-empty one is checked
    # on every subsequent request (including the GET below) and would
    # reject it, since TestClient never presents a real IP
    # (`test_ip_allowlist_blocks_a_request_once_configured` covers that
    # persistence-and-enforcement path directly).
    headers = auth_headers(Role.ORG_ADMIN, "a")
    put_response = client.put(
        "/org-policy",
        json={
            "session_timeout_seconds": 1800,
            "ip_allowlist": [],
            "data_residency_region": "us-east-1",
        },
        headers=headers,
    )
    assert put_response.status_code == 200
    body = put_response.json()
    assert body["session_timeout_seconds"] == 1800
    assert body["ip_allowlist"] == []
    assert body["mfa_required"] is True
    assert body["data_residency_region"] == "us-east-1"
    assert body["updated_at"] is not None

    get_response = client.get("/org-policy", headers=headers)
    assert get_response.status_code == 200
    assert get_response.json() == body


def test_set_org_policy_rejects_a_data_residency_region_over_the_length_limit(
    client: TestClient,
) -> None:
    response = client.put(
        "/org-policy",
        json={
            "session_timeout_seconds": None,
            "ip_allowlist": [],
            "data_residency_region": "x" * 101,
        },
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 422


def test_data_residency_region_is_not_declared_by_default(client: TestClient) -> None:
    headers = auth_headers(Role.ORG_ADMIN, "a")
    response = client.put(
        "/org-policy",
        json={"session_timeout_seconds": None, "ip_allowlist": []},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data_residency_region"] is None


def test_set_org_policy_ignores_any_client_supplied_mfa_required(client: TestClient) -> None:
    """`mfa_required` has no field in `UpdateOrgPolicyIn` at all -- this
    proves a client sending it anyway has no effect (Pydantic silently
    ignores unknown fields by default; there is no code path anywhere
    that reads a client-sent value for it)."""
    response = client.put(
        "/org-policy",
        json={"session_timeout_seconds": None, "ip_allowlist": [], "mfa_required": False},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 200
    assert response.json()["mfa_required"] is True


@pytest.mark.parametrize("value", [0, 30, 90_000])
def test_set_org_policy_rejects_session_timeout_out_of_bounds(
    client: TestClient, value: int
) -> None:
    response = client.put(
        "/org-policy",
        json={"session_timeout_seconds": value, "ip_allowlist": []},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 422


def test_org_policy_is_scoped_per_org(client: TestClient) -> None:
    client.put(
        "/org-policy",
        json={"session_timeout_seconds": 1800, "ip_allowlist": []},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    other_org = client.get("/org-policy", headers=auth_headers(Role.ORG_ADMIN, "b"))
    assert other_org.status_code == 200
    assert other_org.json()["session_timeout_seconds"] is None


def test_login_honors_the_session_timeout_override(
    client: TestClient, repo: FakeRepository
) -> None:
    subject = "policy-login-test@example.com"
    mfa_secret = generate_enrollment_secret()
    user_id = repo.seed_user(subject)
    repo.seed_membership(user_id, TENANT_A, role=Role.BILLER)
    repo.seed_login_credentials(subject, password=_PASSWORD, mfa_secret=mfa_secret)
    repo.set_org_policy(user_id, TENANT_A, session_timeout_seconds=1800, ip_allowlist=[])

    response = client.post(
        "/auth/login",
        json={"subject": subject, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)},
    )
    assert response.status_code == 200
    payload = jwt.decode(response.json()["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == pytest.approx(1800)


def test_login_uses_the_default_ttl_when_no_policy_is_configured(
    client: TestClient, repo: FakeRepository
) -> None:
    subject = "policy-login-default-test@example.com"
    mfa_secret = generate_enrollment_secret()
    user_id = repo.seed_user(subject)
    repo.seed_membership(user_id, TENANT_A, role=Role.BILLER)
    repo.seed_login_credentials(subject, password=_PASSWORD, mfa_secret=mfa_secret)

    response = client.post(
        "/auth/login",
        json={"subject": subject, "password": _PASSWORD, "totp_code": _totp_code(mfa_secret)},
    )
    assert response.status_code == 200
    payload = jwt.decode(response.json()["access_token"], JWT_SECRET, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == pytest.approx(15 * 60)


def test_ip_allowlist_blocks_a_request_once_configured(client: TestClient) -> None:
    """`TestClient` always presents the literal string `"testclient"` as
    the request's client host, never a real IP -- which can never match a
    real IP/CIDR allowlist entry. This environment can therefore only
    prove the *rejection* path over a live HTTP request; the *matching*
    path (a real IP against a real CIDR range) is proven directly against
    `security.ip_allowlist.ip_allowed` in
    `tests/security/test_ip_allowlist.py`."""
    admin_headers = auth_headers(Role.ORG_ADMIN, "a")
    configure = client.put(
        "/org-policy",
        json={"session_timeout_seconds": None, "ip_allowlist": ["203.0.113.5/32"]},
        headers=admin_headers,
    )
    assert configure.status_code == 200

    response = client.get("/organizations/members", headers=admin_headers)
    assert response.status_code == 403
    assert response.json()["message"] == "request origin not permitted by organization policy"


def test_ip_allowlist_does_not_affect_a_different_org(client: TestClient) -> None:
    client.put(
        "/org-policy",
        json={"session_timeout_seconds": None, "ip_allowlist": ["203.0.113.5/32"]},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    response = client.get("/organizations/members", headers=auth_headers(Role.ORG_ADMIN, "b"))
    assert response.status_code == 200
