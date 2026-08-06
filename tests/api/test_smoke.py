"""Quick end-to-end sanity check before the full authz matrix -- if this
doesn't pass, nothing else in tests/api/ will either."""

from __future__ import annotations

from fastapi.testclient import TestClient

from security.rbac import Role
from tests.api.conftest import auth_headers


def test_biller_can_list_own_tenant_findings(client: TestClient) -> None:
    response = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    assert response.status_code == 200
    body = response.json()
    assert body["page"]["total"] == 1
    assert len(body["items"]) == 1


def test_missing_token_is_401(client: TestClient) -> None:
    response = client.get("/findings")
    assert response.status_code == 401


def test_analyst_cannot_upload_remittance(client: TestClient) -> None:
    response = client.post(
        "/remittances",
        headers=auth_headers(Role.ANALYST, "a"),
        files={"file": ("test.835", b"ISA*...", "application/octet-stream")},
    )
    assert response.status_code == 403
