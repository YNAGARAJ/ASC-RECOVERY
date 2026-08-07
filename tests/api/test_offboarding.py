"""Phase 5 step 4 (`docs/MASTER-BUILD-PROMPT-V2.md`): offboarding is the
phase's actual stated gate -- "offboarding test proves instant session
death." `test_revoking_membership_kills_the_next_request_immediately`
below is that proof, at the route/API level: an already-issued, unexpired
token stops working on the very next request, with no token-revocation
list involved -- role/access is resolved fresh from `memberships` on
every request (`api/auth.py::get_auth_context`), and offboarding just
makes that resolution come back empty.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from security.rbac import Action, Role, can
from security.session import issue_session
from tests.api.conftest import JWT_SECRET, TENANT_A, auth_headers
from tests.api.fakes import FakeRepository


def _headers_for(subject: str, org_id: uuid.UUID) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, str(org_id), mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_revoking_membership_kills_the_next_request_immediately(
    client: TestClient, repo: FakeRepository
) -> None:
    target_subject = "offboarded-user@example.com"
    target_user_id = repo.seed_user(target_subject)
    membership_id = repo.seed_membership(target_user_id, TENANT_A, role=Role.BILLER)
    target_headers = _headers_for(target_subject, TENANT_A)

    before = client.get("/findings", headers=target_headers)
    assert before.status_code == 200

    revoke = client.post(
        f"/organizations/members/{membership_id}/revoke",
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert revoke.status_code == 204

    # Same token, same request, no re-login -- must fail now.
    after = client.get("/findings", headers=target_headers)
    assert after.status_code == 401


@pytest.mark.parametrize("role", list(Role))
def test_revoke_membership_matrix(client: TestClient, repo: FakeRepository, role: Role) -> None:
    target_user_id = repo.seed_user(f"revoke-target-{role.value}@example.com")
    membership_id = repo.seed_membership(target_user_id, TENANT_A, role=Role.BILLER)

    response = client.post(
        f"/organizations/members/{membership_id}/revoke", headers=auth_headers(role, "a")
    )
    if not can(role, Action.MANAGE_USERS):
        assert response.status_code == 403, (role, response.text)
        return
    assert response.status_code == 204, (role, response.text)


def test_revoking_an_already_revoked_membership_is_404(
    client: TestClient, repo: FakeRepository
) -> None:
    target_user_id = repo.seed_user("double-revoke@example.com")
    membership_id = repo.seed_membership(target_user_id, TENANT_A, role=Role.BILLER)
    headers = auth_headers(Role.ORG_ADMIN, "a")

    first = client.post(f"/organizations/members/{membership_id}/revoke", headers=headers)
    assert first.status_code == 204
    second = client.post(f"/organizations/members/{membership_id}/revoke", headers=headers)
    assert second.status_code == 404


def test_revoking_an_unknown_membership_is_404(client: TestClient) -> None:
    response = client.post(
        "/organizations/members/00000000-0000-0000-0000-000000000000/revoke",
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 404


def test_cannot_revoke_a_membership_in_a_different_org(
    client: TestClient, repo: FakeRepository
) -> None:
    """The same IDOR-shaped guarantee every other direct-id-lookup route
    gives: a known, real `membership_id` belonging to another org 404s
    exactly like a nonexistent one would."""
    target_user_id = repo.seed_user("tenant-a-target@example.com")
    membership_id = repo.seed_membership(target_user_id, TENANT_A, role=Role.BILLER)

    response = client.post(
        f"/organizations/members/{membership_id}/revoke",
        headers=auth_headers(Role.ORG_ADMIN, "b"),
    )
    assert response.status_code == 404

    # And it's genuinely untouched -- tenant A's own admin can still revoke it.
    still_active = client.post(
        f"/organizations/members/{membership_id}/revoke",
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert still_active.status_code == 204
