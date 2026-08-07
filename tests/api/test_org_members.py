"""GET /organizations/members (Phase 5 step 2) -- delegated admin's
read surface. Role gating mirrors test_authz_matrix.py's shape, but
against this endpoint's actual seeded data: `conftest.py`'s `repo`
fixture seeds one membership per `Role` in each of the two tenants, so an
allowed caller sees exactly `len(Role)` members for their own org and
nothing from the other tenant -- no `org_id` param exists to manipulate
(see `api/routes/organizations.py`'s docstring for why), so cross-tenant
isolation here reduces to "the response never contains the other
tenant's subjects."
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from security.rbac import Action, Role, can
from tests.api.conftest import auth_headers, subject_for


@pytest.mark.parametrize("role", list(Role))
def test_list_org_members_matrix(client: TestClient, role: Role) -> None:
    for tenant_label in ("a", "b"):
        response = client.get("/organizations/members", headers=auth_headers(role, tenant_label))
        if not can(role, Action.MANAGE_USERS):
            assert response.status_code == 403, (role, tenant_label)
            continue
        assert response.status_code == 200, (role, tenant_label, response.text)
        body = response.json()
        assert body["page"]["total"] == len(list(Role))
        subjects = {item["subject"] for item in body["items"]}
        assert subjects == {subject_for(r, tenant_label) for r in Role}


@pytest.mark.parametrize("role", list(Role))
def test_list_org_members_never_leaks_other_tenant(client: TestClient, role: Role) -> None:
    if not can(role, Action.MANAGE_USERS):
        pytest.skip("role cannot call this endpoint at all")
    response = client.get("/organizations/members", headers=auth_headers(role, "a"))
    assert response.status_code == 200
    subjects = {item["subject"] for item in response.json()["items"]}
    assert subjects
    assert all("-a-" in subject for subject in subjects)
    assert not any("-b-" in subject for subject in subjects)


def test_list_org_members_pagination(client: TestClient) -> None:
    first_page = client.get(
        "/organizations/members",
        params={"limit": 3, "offset": 0},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )
    second_page = client.get(
        "/organizations/members",
        params={"limit": 3, "offset": 3},
        headers=auth_headers(Role.ORG_ADMIN, "a"),
    )

    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["page"] == {"total": len(list(Role)), "limit": 3, "offset": 0}
    assert len(first_body["items"]) == 3

    second_body = second_page.json()
    assert second_body["page"]["offset"] == 3
    assert len(second_body["items"]) == 3

    first_ids = {item["membership_id"] for item in first_body["items"]}
    second_ids = {item["membership_id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_list_org_members_includes_facility_ids_for_specific_facilities_scope(
    client: TestClient,
) -> None:
    """Distinguishes `ALL_FACILITIES` (empty `facility_ids` -- access is
    the whole resolved subtree, not "no facilities") from
    `SPECIFIC_FACILITIES` (populated `facility_ids`), per
    `db.repository.OrgMember`'s docstring."""
    response = client.get("/organizations/members", headers=auth_headers(Role.ORG_ADMIN, "a"))
    assert response.status_code == 200
    items = response.json()["items"]
    all_facilities_member = next(
        item for item in items if item["subject"] == subject_for(Role.ORG_ADMIN, "a")
    )
    assert all_facilities_member["scope"] == "ALL_FACILITIES"
    assert all_facilities_member["facility_ids"] == []
