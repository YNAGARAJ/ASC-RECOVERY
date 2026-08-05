"""Pagination on every list endpoint, per the Phase 6 prompt."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from api.repository import FindingDetail, FindingSummary, ServiceLineInfo
from security.rbac import Role
from tests.api.conftest import TENANT_A, auth_headers
from tests.api.fakes import FakeRepository, now


def _extra_finding(label: str) -> FindingDetail:
    summary = FindingSummary(
        id=uuid.uuid4(),
        claim_id=uuid.uuid4(),
        line_index=0,
        procedure_code="99214",
        expected_allowed="200.00",
        actual_allowed="150.00",
        shortfall="50.00",
        root_cause="UNDETERMINED_VARIANCE",
        rule_version="2026.08.0",
        created_at=now(),
        outcome=None,
        amount_recovered=None,
        outcome_recorded_by=None,
        outcome_recorded_at=None,
    )
    return FindingDetail(
        summary=summary,
        evidence=label,
        patient_control_number="PCN-EXTRA",
        payer_claim_control_number="PAYERCTRL-EXTRA",
        date_of_service=date(2023, 2, 1),
        patient_name=None,
        patient_member_id=None,
        service_line=ServiceLineInfo(
            line_index=0,
            procedure_code="99214",
            modifiers=[],
            charge="500.00",
            allowed="450.00",
            paid_computed="400.00",
            service_date=date(2023, 2, 1),
        ),
        adjustments=[],
        confidence_score=None,
    )


def test_limit_and_offset_are_respected(client: TestClient, repo: FakeRepository) -> None:
    for i in range(4):
        repo.seed_finding(TENANT_A, _extra_finding(f"extra-{i}"))
    # 1 from conftest's base seed + 4 extra = 5 total for tenant A.

    first_page = client.get(
        "/findings", params={"limit": 2, "offset": 0}, headers=auth_headers(Role.BILLER, "a")
    )
    second_page = client.get(
        "/findings", params={"limit": 2, "offset": 2}, headers=auth_headers(Role.BILLER, "a")
    )

    assert first_page.status_code == 200
    first_body = first_page.json()
    assert first_body["page"] == {"total": 5, "limit": 2, "offset": 0}
    assert len(first_body["items"]) == 2

    second_body = second_page.json()
    assert second_body["page"] == {"total": 5, "limit": 2, "offset": 2}
    assert len(second_body["items"]) == 2

    first_ids = {item["id"] for item in first_body["items"]}
    second_ids = {item["id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_limit_is_capped(client: TestClient) -> None:
    response = client.get(
        "/findings", params={"limit": 500}, headers=auth_headers(Role.BILLER, "a")
    )
    assert response.status_code == 422  # exceeds the le=100 cap


def test_default_pagination_applies_when_unspecified(client: TestClient) -> None:
    response = client.get("/findings", headers=auth_headers(Role.BILLER, "a"))
    assert response.status_code == 200
    assert response.json()["page"] == {"total": 1, "limit": 20, "offset": 0}
