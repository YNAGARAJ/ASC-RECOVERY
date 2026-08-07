"""Phase 5 step 5 (`docs/MASTER-BUILD-PROMPT-V2.md`): API keys. Create,
list (masked), revoke follow the exact same delegated-admin shape as
`test_org_members.py`/`test_offboarding.py`; the distinctive part this
file proves is the other half -- a created key's raw value actually
authenticates a request through `api/auth.py`'s API-key branch, and a
revoked or expired one is rejected exactly like a dead JWT would be.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from security.rbac import Action, Role, can
from security.tokens import API_KEY_PREFIX
from tests.api.conftest import TENANT_A, SeedIds, auth_headers
from tests.api.fakes import FakeRepository


def _create(
    client: TestClient, *, role: Role = Role.ORG_ADMIN, tenant: str = "a", **body: object
) -> Response:
    payload = {"name": "CI integration", **body}
    return client.post("/api-keys", json=payload, headers=auth_headers(role, tenant))


@pytest.mark.parametrize("role", list(Role))
def test_create_api_key_matrix(client: TestClient, role: Role) -> None:
    response = _create(client, role=role)
    if not can(role, Action.MANAGE_USERS):
        assert response.status_code == 403, (role, response.text)
        return
    assert response.status_code == 201, (role, response.text)
    body = response.json()
    assert body["key"].startswith(API_KEY_PREFIX)
    assert body["name"] == "CI integration"
    assert body["scope"] == "ALL_FACILITIES"


def test_create_api_key_requires_facility_ids_for_specific_facilities_scope(
    client: TestClient,
) -> None:
    response = _create(client, scope="SPECIFIC_FACILITIES")
    assert response.status_code == 400


def test_list_api_keys_never_exposes_the_raw_key_or_its_hash(client: TestClient) -> None:
    created = _create(client)
    assert created.status_code == 201
    raw_key = created.json()["key"]

    listed = client.get("/api-keys", headers=auth_headers(Role.ORG_ADMIN, "a"))
    assert listed.status_code == 200
    body = listed.json()
    assert body["items"][0]["id"] == created.json()["id"]
    assert body["items"][0]["name"] == "CI integration"
    assert raw_key not in listed.text


def test_revoking_an_api_key_is_a_404_on_second_attempt(client: TestClient) -> None:
    created = _create(client)
    api_key_id = created.json()["id"]
    headers = auth_headers(Role.ORG_ADMIN, "a")

    first = client.post(f"/api-keys/{api_key_id}/revoke", headers=headers)
    assert first.status_code == 204
    second = client.post(f"/api-keys/{api_key_id}/revoke", headers=headers)
    assert second.status_code == 404


def test_revoking_an_unknown_api_key_is_404(client: TestClient) -> None:
    response = client.post(
        "/api-keys/00000000-0000-0000-0000-000000000000/revoke",
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    assert response.status_code == 404


def test_cannot_revoke_an_api_key_in_a_different_org(client: TestClient) -> None:
    created = _create(client, tenant="a")
    api_key_id = created.json()["id"]

    cross_org = client.post(
        f"/api-keys/{api_key_id}/revoke", headers=auth_headers(Role.ORG_ADMIN, "b")
    )
    assert cross_org.status_code == 404

    same_org = client.post(
        f"/api-keys/{api_key_id}/revoke", headers=auth_headers(Role.ORG_ADMIN, "a")
    )
    assert same_org.status_code == 204


def test_a_freshly_created_api_key_authenticates_requests(client: TestClient) -> None:
    created = _create(client)
    raw_key = created.json()["key"]

    response = client.get("/findings", headers={"Authorization": f"Bearer {raw_key}"})
    assert response.status_code == 200


def test_a_revoked_api_key_is_rejected(client: TestClient) -> None:
    created = _create(client)
    raw_key = created.json()["key"]
    api_key_id = created.json()["id"]

    revoke = client.post(
        f"/api-keys/{api_key_id}/revoke", headers=auth_headers(Role.ORG_ADMIN, "a")
    )
    assert revoke.status_code == 204

    response = client.get("/findings", headers={"Authorization": f"Bearer {raw_key}"})
    assert response.status_code == 401


def test_an_expired_api_key_is_rejected(client: TestClient, repo: FakeRepository) -> None:
    service_user_id = repo.seed_user("api-key:expired-test")
    repo.seed_membership(service_user_id, TENANT_A, role=Role.API_SERVICE)
    raw_key = repo.seed_api_key(
        TENANT_A,
        service_user_id,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    response = client.get("/findings", headers={"Authorization": f"Bearer {raw_key}"})
    assert response.status_code == 401


def test_an_unknown_api_key_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/findings", headers={"Authorization": f"Bearer {API_KEY_PREFIX}not-a-real-key"}
    )
    assert response.status_code == 401


def test_api_key_scoped_to_one_org_cannot_reach_the_other(
    client: TestClient, seed_ids: SeedIds
) -> None:
    created = _create(client, tenant="a")
    raw_key = created.json()["key"]

    response = client.get("/findings", headers={"Authorization": f"Bearer {raw_key}"})
    assert response.status_code == 200
    claim_ids = {item["claim_id"] for item in response.json()["items"]}
    assert str(seed_ids.claim_a) in claim_ids
    assert str(seed_ids.claim_b) not in claim_ids
