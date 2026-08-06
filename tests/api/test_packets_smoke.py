"""Quick end-to-end sanity check for the packet generate/list/approve/
reject endpoints before the full authz matrix."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from security.rbac import Role
from tests.api.conftest import SeedIds, auth_headers


def test_biller_can_generate_list_and_approve_a_packet(
    client: TestClient, seed_ids: SeedIds
) -> None:
    generate = client.post(
        f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(Role.BILLER, "a")
    )
    assert generate.status_code == 201
    packet_id = generate.json()["id"]
    assert generate.json()["status"] == "draft"

    listing = client.get(
        f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(Role.BILLER, "a")
    )
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1

    approve = client.post(
        f"/packets/{packet_id}/approve", headers=auth_headers(Role.BILLER, "a")
    )
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    assert approve.json()["decided_by"] is not None


def test_analyst_cannot_generate_a_packet(client: TestClient, seed_ids: SeedIds) -> None:
    response = client.post(
        f"/findings/{seed_ids.finding_a}/packets", headers=auth_headers(Role.ANALYST, "a")
    )
    assert response.status_code == 403


def test_generating_a_packet_for_an_unknown_finding_is_404(client: TestClient) -> None:
    response = client.post(
        f"/findings/{uuid.uuid4()}/packets", headers=auth_headers(Role.BILLER, "a")
    )
    assert response.status_code == 404
