"""Shared fixtures for tests/api/: an app wired to a FakeRepository seeded
with two organizations' worth of data (each with one facility), and
helpers to mint real JWTs per role via `issue_session` (Phase 4) -- the
auth path itself stays real; only the Postgres-backed data layer is
faked. `auth_headers` no longer picks a role directly (the JWT carries no
role claim -- see `security.session`'s module docstring); it selects
*which org's membership* to activate, and the seeded membership's role
for that (subject, org) pair is what `resolve_membership_role` returns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import create_app
from api.repository import (
    AuditLogEntry,
    ContractSummary,
    FindingDetail,
    FindingSummary,
    ServiceLineInfo,
)
from security.rbac import Role
from security.session import issue_session
from tests.api.fakes import FakeRepository, now

JWT_SECRET = "test-only-secret-never-use-in-production"

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


def subject_for(role: Role, tenant_label: str) -> str:
    return f"user-{tenant_label}-{role.value}"


@dataclass(frozen=True, slots=True)
class SeedIds:
    finding_a: uuid.UUID
    finding_b: uuid.UUID
    claim_a: uuid.UUID
    claim_b: uuid.UUID
    contract_a: uuid.UUID
    contract_b: uuid.UUID


def _finding_detail(tenant_label: str, claim_id: uuid.UUID) -> FindingDetail:
    summary = FindingSummary(
        id=uuid.uuid4(),
        claim_id=claim_id,
        line_index=0,
        procedure_code="99213",
        expected_allowed="100.00",
        actual_allowed="50.00",
        shortfall="50.00",
        root_cause="MPPR_NOT_APPLIED",
        rule_version="2026.08.0",
        created_at=now(),
        outcome=None,
        amount_recovered=None,
        outcome_recorded_by=None,
        outcome_recorded_at=None,
    )
    return FindingDetail(
        summary=summary,
        evidence=f"{tenant_label} evidence string",
        patient_control_number="PCN-1",
        payer_claim_control_number="PAYERCTRL-1",
        date_of_service=date(2023, 1, 10),
        patient_name=f"{tenant_label.upper()} PATIENT",
        patient_member_id="MBR-1",
        service_line=ServiceLineInfo(
            line_index=0,
            procedure_code="99213",
            modifiers=[],
            charge="500.00",
            allowed="450.00",
            paid_computed="430.00",
            service_date=date(2023, 1, 10),
        ),
        adjustments=[],
        confidence_score=None,
    )


@pytest.fixture
def seed_ids() -> SeedIds:
    return SeedIds(
        finding_a=uuid.uuid4(),
        finding_b=uuid.uuid4(),
        claim_a=uuid.uuid4(),
        claim_b=uuid.uuid4(),
        contract_a=uuid.uuid4(),
        contract_b=uuid.uuid4(),
    )


@pytest.fixture
def repo(seed_ids: SeedIds) -> FakeRepository:
    repository = FakeRepository()

    # Two independent single-facility orgs ("tenant" in every other
    # fixture name here, kept for continuity with existing test bodies --
    # each org IS the facility's sole owner, matching the common
    # single-facility ASC case org/facility resolution collapses to).
    org_a = repository.seed_organization(org_id=TENANT_A)
    org_b = repository.seed_organization(org_id=TENANT_B)
    facility_a = repository.seed_facility(org_a, facility_id=TENANT_A)
    facility_b = repository.seed_facility(org_b, facility_id=TENANT_B)

    # Force the seeded finding ids to match seed_ids exactly, rather than
    # whatever random id FindingSummary's own construction chose, so tests
    # can request a specific tenant's finding by id deliberately.
    detail_a = _finding_detail("tenant-a", seed_ids.claim_a)
    detail_a = replace(detail_a, summary=replace(detail_a.summary, id=seed_ids.finding_a))
    detail_b = _finding_detail("tenant-b", seed_ids.claim_b)
    detail_b = replace(detail_b, summary=replace(detail_b.summary, id=seed_ids.finding_b))
    repository.seed_finding(facility_a, detail_a)
    repository.seed_finding(facility_b, detail_b)

    repository.seed_contract(
        org_a,
        ContractSummary(
            id=seed_ids.contract_a, payer_id="PAYER-A", name="Tenant A Contract", created_at=now()
        ),
    )
    repository.seed_contract(
        org_b,
        ContractSummary(
            id=seed_ids.contract_b, payer_id="PAYER-B", name="Tenant B Contract", created_at=now()
        ),
    )

    for role in Role:
        user_a = repository.seed_user(subject_for(role, "a"))
        repository.seed_membership(user_a, org_a, role=role)
        user_b = repository.seed_user(subject_for(role, "b"))
        repository.seed_membership(user_b, org_b, role=role)

    repository.seed_audit_entry(
        facility_a,
        AuditLogEntry(
            id=uuid.uuid4(),
            actor="tenant-a-actor",
            action="remittance_ingested",
            resource_type="remittance",
            resource_id=str(uuid.uuid4()),
            occurred_at=now(),
            source_ip=None,
            phi_accessed=True,
            request_id=None,
        ),
    )
    repository.seed_audit_entry(
        facility_b,
        AuditLogEntry(
            id=uuid.uuid4(),
            actor="tenant-b-actor",
            action="remittance_ingested",
            resource_type="remittance",
            resource_id=str(uuid.uuid4()),
            occurred_at=now(),
            source_ip=None,
            phi_accessed=True,
            request_id=None,
        ),
    )

    return repository


@pytest.fixture
def app(repo: FakeRepository) -> FastAPI:
    return create_app(repository=repo, jwt_secret_key=JWT_SECRET)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def auth_headers(role: Role, tenant_label: str) -> dict[str, str]:
    """`tenant_label` ("a"/"b") selects which seeded org's membership to
    activate -- the org id doubles as the facility id in this fixture set
    (`TENANT_A`/`TENANT_B`), matching `repo`'s single-facility-per-org
    seeding above."""
    subject = subject_for(role, tenant_label)
    active_org_id = TENANT_A if tenant_label == "a" else TENANT_B
    tokens = issue_session(JWT_SECRET, subject, str(active_org_id), mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}
