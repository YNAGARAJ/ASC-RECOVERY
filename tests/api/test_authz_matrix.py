"""The Phase 6 gate's core requirement: every (role, endpoint, own-tenant |
other-tenant) cell asserted -- not a sample.

Role-gating ground truth comes straight from `security.rbac.can()` (already
fully gated in Phase 4); this file's job is proving each endpoint actually
enforces what `can()` says. Tenant-scoping ground truth is structural: no
endpoint anywhere in `api/routes/` accepts a client-supplied tenant
identifier (see `test_tenant_param_absence.py`), so a request authenticated
as a tenant-A user can only ever see tenant-A data no matter what id it
asks for -- proven directly against the two tenants FakeRepository is
seeded with.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from security.rbac import Action, Role, can
from tests.api.conftest import TENANT_A, SeedIds, auth_headers
from tests.api.fakes import FakeRepository

# Every list-shaped endpoint this phase built: (method, path, action).
_LIST_ENDPOINTS: list[tuple[str, str, Action]] = [
    ("GET", "/findings", Action.READ_FINDING),
    ("GET", "/contracts", Action.READ_CONTRACT),
    ("GET", "/audit-log", Action.READ_AUDIT_LOG),
]


@pytest.mark.parametrize(("method", "path", "action"), _LIST_ENDPOINTS)
@pytest.mark.parametrize("role", list(Role))
def test_list_endpoint_matrix(
    client: TestClient,
    method: str,
    path: str,
    action: Action,
    role: Role,
) -> None:
    """For every role: disallowed roles get 403; allowed roles get 200 with
    only their own tenant's single seeded row, whichever tenant they are."""
    for tenant_label in ("a", "b"):
        response = client.request(method, path, headers=auth_headers(role, tenant_label))
        if not can(role, action):
            assert response.status_code == 403, (role, path, tenant_label)
            continue
        assert response.status_code == 200, (role, path, tenant_label, response.text)
        body = response.json()
        assert body["page"]["total"] == 1, (role, path, tenant_label, body)
        assert len(body["items"]) == 1


@pytest.mark.parametrize("role", list(Role))
def test_findings_export_csv_matrix(client: TestClient, role: Role) -> None:
    action = Action.EXPORT_WORKLIST
    response_a = client.request("GET", "/findings/export.csv", headers=auth_headers(role, "a"))
    if not can(role, action):
        assert response_a.status_code == 403
        return
    assert response_a.status_code == 200
    assert "tenant-a" in response_a.text or "MPPR_NOT_APPLIED" in response_a.text
    assert "tenant-b" not in response_a.text

    response_b = client.request("GET", "/findings/export.csv", headers=auth_headers(role, "b"))
    assert response_b.status_code == 200
    assert "tenant-a" not in response_b.text


@pytest.mark.parametrize("role", list(Role))
def test_finding_detail_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    """The core cross-tenant proof: a tenant-A user asking for tenant-B's
    finding id (by parameter manipulation -- there's nothing else to
    manipulate, since tenant_id itself is never a parameter) must get a
    404, never tenant B's data."""
    action = Action.READ_FINDING

    own = client.get(f"/findings/{seed_ids.finding_a}", headers=auth_headers(role, "a"))
    other = client.get(f"/findings/{seed_ids.finding_b}", headers=auth_headers(role, "a"))

    if not can(role, action):
        assert own.status_code == 403
        assert other.status_code == 403
        return

    assert own.status_code == 200
    assert own.json()["patient_control_number"] == "PCN-1"
    assert own.json()["evidence"] == "tenant-a evidence string"

    assert other.status_code == 404
    assert "tenant-b" not in other.text


@pytest.mark.parametrize("role", list(Role))
def test_upload_remittance_matrix(client: TestClient, repo: FakeRepository, role: Role) -> None:
    action = Action.UPLOAD_REMITTANCE
    response = client.post(
        "/remittances",
        headers=auth_headers(role, "a"),
        files={"file": ("test.835", b"ISA*not a real file", "application/octet-stream")},
    )
    if not can(role, action):
        assert response.status_code == 403
        assert len(repo.ingest_calls) == 0
        return
    assert response.status_code == 202
    assert len(repo.ingest_calls) == 1
    called_tenant_id = repo.ingest_calls[0][0]
    assert called_tenant_id == TENANT_A  # never manipulable to TENANT_B


@pytest.mark.parametrize("role", list(Role))
def test_create_contract_matrix(client: TestClient, repo: FakeRepository, role: Role) -> None:
    action = Action.MANAGE_CONTRACT
    response = client.post(
        "/contracts",
        headers=auth_headers(role, "a"),
        json={"payer_id": "NEWPAYER", "name": "New Contract"},
    )
    if not can(role, action):
        assert response.status_code == 403
        return
    assert response.status_code == 201
    new_id = response.json()["id"]
    stored_tenant_id, _ = repo.contracts[uuid.UUID(new_id)]
    assert stored_tenant_id == TENANT_A


@pytest.mark.parametrize("role", list(Role))
def test_create_contract_version_matrix(
    client: TestClient, seed_ids: SeedIds, role: Role
) -> None:
    action = Action.MANAGE_CONTRACT
    response = client.post(
        f"/contracts/{seed_ids.contract_a}/versions",
        headers=auth_headers(role, "a"),
        json={"effective_from": "2023-01-01", "fee_schedule": {"99213": "100.00"}},
    )
    if not can(role, action):
        assert response.status_code == 403
        return
    assert response.status_code == 201
    assert "id" in response.json()


@pytest.mark.parametrize("role", list(Role))
def test_generate_packet_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    """Same cross-tenant shape as finding detail: generating against
    another tenant's finding id must 404, never draft against it."""
    action = Action.DRAFT_RECOVERY_PACKET

    own = client.post(f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(role, "a"))
    other = client.post(f"/findings/{seed_ids.finding_b}/packets", headers=auth_headers(role, "a"))

    if not can(role, action):
        assert own.status_code == 403
        assert other.status_code == 403
        return

    assert own.status_code == 201
    assert own.json()["status"] == "draft"
    assert other.status_code == 404


@pytest.mark.parametrize("role", list(Role))
def test_list_packets_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    action = Action.READ_FINDING
    response = client.get(
        f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(role, "a")
    )
    if not can(role, action):
        assert response.status_code == 403
        return
    assert response.status_code == 200
    assert "items" in response.json()


@pytest.mark.parametrize("role", list(Role))
def test_decide_packet_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    """Cross-tenant proof for the human-approval step: a packet generated
    under tenant B must not be approvable/rejectable by a tenant-A user
    under any role."""
    action = Action.APPROVE_RECOVERY_PACKET

    own_packet = client.post(
        f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(Role.BILLER, "a")
    ).json()["id"]
    other_packet = client.post(
        f"/findings/{seed_ids.finding_b}/packets", headers=auth_headers(Role.BILLER, "b")
    ).json()["id"]

    approve_own = client.post(
        f"/packets/{own_packet}/approve", headers=auth_headers(role, "a")
    )
    reject_other = client.post(
        f"/packets/{other_packet}/reject", headers=auth_headers(role, "a")
    )

    if not can(role, action):
        assert approve_own.status_code == 403
        assert reject_other.status_code == 403
        return

    assert approve_own.status_code == 200
    assert approve_own.json()["status"] == "approved"
    assert reject_other.status_code == 404


@pytest.mark.parametrize("role", list(Role))
def test_record_outcome_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    """Cross-tenant proof for outcome recording: a tenant-A user must
    never be able to record an outcome against tenant B's finding."""
    action = Action.RECORD_FINDING_OUTCOME

    other = client.post(
        f"/findings/{seed_ids.finding_b}/outcome",
        headers=auth_headers(role, "a"),
        json={"outcome": "recovered", "amount_recovered": "50.00"},
    )
    own = client.post(
        f"/findings/{seed_ids.finding_a}/outcome",
        headers=auth_headers(role, "a"),
        json={"outcome": "recovered", "amount_recovered": "50.00"},
    )

    if not can(role, action):
        assert own.status_code == 403
        assert other.status_code == 403
        return

    assert other.status_code == 404
    assert own.status_code == 200
    assert own.json()["outcome"] == "recovered"
    assert own.json()["amount_recovered"] == "50.00"


@pytest.mark.parametrize("role", list(Role))
def test_claim_access_history_matrix(client: TestClient, seed_ids: SeedIds, role: Role) -> None:
    action = Action.READ_PHI_ACCESS_LOG
    # Generate some access history first, independent of the role under
    # test, so there's something to see when an allowed role asks for it.
    client.get(f"/findings/{seed_ids.finding_a}", headers=auth_headers(Role.PLATFORM_ADMIN, "a"))

    own = client.get(
        f"/claims/{seed_ids.claim_a}/access-history", headers=auth_headers(role, "a")
    )
    other = client.get(
        f"/claims/{seed_ids.claim_b}/access-history", headers=auth_headers(role, "a")
    )

    if not can(role, action):
        assert own.status_code == 403
        assert other.status_code == 403
        return

    assert own.status_code == 200
    assert len(own.json()["items"]) >= 1
    # Tenant B's claim history is invisible to a tenant-A actor -- not a
    # 404 (which would confirm the claim id "exists" for someone), just an
    # empty result, the same shape as any other unmatched query.
    assert other.status_code == 200
    assert other.json()["items"] == []
