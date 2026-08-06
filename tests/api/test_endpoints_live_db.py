"""End-to-end proof against real Postgres + real RLS, for two
representative endpoints (list findings, finding detail). Confirms that
FakeRepository's tenant-isolation guarantee -- proven exhaustively in
test_authz_matrix.py without a database -- matches what actually happens
against a live one.

Skips cleanly without `TEST_DATABASE_URL`, same pattern as
tests/db/conftest.py and tests/ingestion/conftest.py. Written here, never
executed in this environment (no Docker/WSL/Postgres available) --
honest about being unverified, same as the rest of this repo's DB-backed
test files.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.repository import PostgresRepository
from db import repository as db_repository
from db.base import make_engine, make_session_factory
from db.tenancy import tenant_session
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from packets.drafter import ScriptedPacketDrafter
from packets.prompt import (
    ACTUAL_ALLOWED_TOKEN,
    EXPECTED_ALLOWED_TOKEN,
    PATIENT_TOKEN,
    SHORTFALL_TOKEN,
)
from security.rbac import Role
from security.session import issue_session
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
JWT_SECRET = "test-only-secret-never-use-in-production"

# One shared encryptor for this whole file -- ingestion (writing, in
# _seed_tenant) and every PostgresRepository built below (reading) must use
# the same KEK, exactly like production uses one encryptor app-wide. A
# fresh make_test_encryptor() per call would wrap DEKs under a different,
# unrelated in-memory KEK each time, and decryption would fail.
_TEST_ENCRYPTOR = make_test_encryptor()


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/api/test_endpoints_live_db.py "
            "needs a live Postgres 16 with migrations applied, connected as the "
            "asc_app role. See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


def _seed_tenant(session_factory: sessionmaker[Session], label: str) -> tuple[uuid.UUID, str]:
    """Returns (tenant_id, subject) -- subject is unique-constrained on the
    `users` table, so it's generated here (not caller-supplied) to survive
    repeated runs against a persistent test database."""
    subject = f"live-user-{label}-{uuid.uuid4().hex[:12]}"
    with session_factory() as session, session.begin():
        tenant = db_repository.create_tenant(session, f"Live tenant {label} {uuid.uuid4()}")
        db_repository.create_user(session, tenant.id, subject=subject, role=Role.BILLER.value)
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        contract = db_repository.create_contract(
            session, tenant_id, TEST_PAYER, f"{label} contract"
        )
        db_repository.create_contract_version(
            session, tenant_id, contract.id, make_contract_version()
        )
        outcome = ingest_file(
            session,
            tenant_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by=subject,
            scanner=EicarAwareScanner(),
            encryptor=_TEST_ENCRYPTOR,
        )
        assert outcome.status == "ingested"  # type: ignore[union-attr]

    return tenant_id, subject


@pytest.fixture
def live_client(app_session_factory: sessionmaker[Session]) -> TestClient:
    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    return TestClient(app)


def _auth_headers(subject: str) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, Role.BILLER, mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def _seed_second_user(
    session_factory: sessionmaker[Session], tenant_id: uuid.UUID, label: str, *, role: Role
) -> str:
    """A second user on an already-seeded tenant, for tests that need to
    exercise an endpoint gated to a role `_seed_tenant`'s single BILLER
    user doesn't have. A real user row is required, matching the token's
    role claim: `api/auth.py::get_auth_context` deliberately rejects a
    token whose role claim disagrees with the DB user's actual role (401
    "role assignment has changed" -- a safeguard against a stale token
    surviving a real role change) -- so minting a same-subject token with
    a *different* role than that subject's actual DB row (the original,
    broken approach here) can never work."""
    subject = f"live-user-{label}-{uuid.uuid4().hex[:12]}"
    with session_factory() as session, session.begin():
        db_repository.create_user(session, tenant_id, subject=subject, role=role.value)
    return subject


def _auth_headers_as(subject: str, role: Role) -> dict[str, str]:
    """Like `_auth_headers`, but for a subject whose actual DB-assigned
    role (see `_seed_second_user`) isn't BILLER -- `role` here must match
    that row, or `get_auth_context`'s consistency check rejects it."""
    tokens = issue_session(JWT_SECRET, subject, role, mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_findings_list_returns_only_own_tenant_row(
    live_client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    _, subject_a = _seed_tenant(app_session_factory, "list-a")
    _seed_tenant(app_session_factory, "list-b")

    response = live_client.get("/findings", headers=_auth_headers(subject_a))
    assert response.status_code == 200
    assert response.json()["page"]["total"] == 1


def test_finding_detail_cross_tenant_lookup_is_404_against_real_rls(
    live_client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    _, subject_a = _seed_tenant(app_session_factory, "detail-a")
    _, subject_b = _seed_tenant(app_session_factory, "detail-b")

    own_list = live_client.get("/findings", headers=_auth_headers(subject_a))
    other_list = live_client.get("/findings", headers=_auth_headers(subject_b))
    other_finding_id = other_list.json()["items"][0]["id"]

    cross_tenant_response = live_client.get(
        f"/findings/{other_finding_id}", headers=_auth_headers(subject_a)
    )

    assert own_list.json()["page"]["total"] == 1
    assert cross_tenant_response.status_code == 404


_VALID_SCRIPTED_DRAFT = (
    f"Dear Sir/Madam, {PATIENT_TOKEN} was underpaid on this claim. Expected "
    f"{EXPECTED_ALLOWED_TOKEN}, paid {ACTUAL_ALLOWED_TOKEN}, shortfall "
    f"{SHORTFALL_TOKEN}. Sincerely,"
)


def test_generate_and_approve_packet_round_trip_against_real_postgres(
    app_session_factory: sessionmaker[Session],
) -> None:
    """The Phase 7 human-approval state machine, end to end: generate a
    draft (real currency validation against a real ingested finding, real
    RLS-scoped persistence) -> approve it -> confirm both steps left an
    audit trail."""
    tenant_id, subject = _seed_tenant(app_session_factory, "packet-a")
    repository = PostgresRepository(
        app_session_factory,
        drafter=ScriptedPacketDrafter([_VALID_SCRIPTED_DRAFT]),
        encryptor=_TEST_ENCRYPTOR,
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)

    findings = client.get("/findings", headers=_auth_headers(subject))
    finding_id = findings.json()["items"][0]["id"]

    generated = client.post(f"/findings/{finding_id}/packets", headers=_auth_headers(subject))
    assert generated.status_code == 201
    assert generated.json()["status"] == "draft"
    packet_id = generated.json()["id"]

    approved = client.post(f"/packets/{packet_id}/approve", headers=_auth_headers(subject))
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by"] == subject

    with tenant_session(app_session_factory, tenant_id) as session:
        _, generated_count = db_repository.list_audit_log(
            session, tenant_id, action="packet_generated", limit=10, offset=0
        )
        _, approved_count = db_repository.list_audit_log(
            session, tenant_id, action="packet_approved", limit=10, offset=0
        )
    assert generated_count == 1
    assert approved_count == 1


def test_recording_a_finding_outcome_writes_an_audit_entry(
    app_session_factory: sessionmaker[Session],
) -> None:
    """F-12 (docs/audit/REGISTER.md): record_finding_outcome writes to
    findings.outcome/amount_recovered -- a PHI-bearing table, including a
    dollar amount -- with no audit_log call at all before this fix, a
    direct CLAUDE.md rule 5 violation ("every write to a PHI-bearing
    table goes through the audit log, no exceptions")."""
    tenant_id, subject = _seed_tenant(app_session_factory, "outcome-audit-a")
    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)

    findings = client.get("/findings", headers=_auth_headers(subject))
    finding_id = findings.json()["items"][0]["id"]

    recorded = client.post(
        f"/findings/{finding_id}/outcome",
        json={"outcome": "recovered", "amount_recovered": "45.00"},
        headers=_auth_headers(subject),
    )
    assert recorded.status_code == 200
    assert recorded.json()["outcome"] == "recovered"

    with tenant_session(app_session_factory, tenant_id) as session:
        entries, count = db_repository.list_audit_log(
            session, tenant_id, action="finding_outcome_recorded", limit=10, offset=0
        )
    assert count == 1
    assert entries[0].actor == subject
    assert entries[0].resource_type == "finding"
    assert entries[0].resource_id == finding_id
    assert entries[0].phi_accessed is True


def test_viewing_a_finding_shows_up_in_its_claim_access_history(
    app_session_factory: sessionmaker[Session],
) -> None:
    """Phase 8 gate: the audit report reconstructs a full access history
    for a given claim. Viewing a finding's PHI-bearing detail writes a
    phi_access_log entry; this proves it's actually queryable back out
    through the auditor-facing endpoint, against real Postgres."""
    tenant_id, subject = _seed_tenant(app_session_factory, "access-history-a")
    admin_subject = _seed_second_user(
        app_session_factory, tenant_id, "access-history-a-admin", role=Role.ADMIN
    )
    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)

    findings = client.get("/findings", headers=_auth_headers(subject))
    finding_id = findings.json()["items"][0]["id"]

    detail = client.get(f"/findings/{finding_id}", headers=_auth_headers(subject))
    assert detail.status_code == 200
    claim_id = detail.json()["finding"]["claim_id"]
    # Phase 10: patient_name/patient_member_id round-trip through
    # EnvelopeEncryptor -- the DB column never holds this plaintext (see
    # tests/db/test_patient_columns_are_encrypted.py for the "never
    # plaintext on disk" half of this proof).
    assert detail.json()["patient_name"] == "PATIENT ONE"
    assert detail.json()["patient_member_id"] == "TESTMBR000001"

    history = client.get(
        f"/claims/{claim_id}/access-history", headers=_auth_headers_as(admin_subject, Role.ADMIN)
    )
    assert history.status_code == 200, (
        f"expected 200, got {history.status_code}: {history.text!r} -- "
        f"admin_subject={admin_subject!r}, "
        f"db row={repository.get_user_by_subject(admin_subject)!r}"
    )
    events = history.json()["items"]
    # phi_access_log-sourced events always carry the generic literal
    # action="phi_access" (db.repository.get_claim_access_history) -- the
    # specific reason lives in `purpose` (phi_access_log's own column,
    # set by write_phi_access_log(..., purpose="finding_detail_view")),
    # not `action`. Checking `action` here was a pre-existing bug: it
    # could never match any real event, since nothing ever sets
    # action="finding_detail_view".
    assert any(
        event["action"] == "phi_access"
        and event["purpose"] == "finding_detail_view"
        and event["actor"] == subject
        for event in events
    ), events
    # Phase 10: ingestion itself (not just later views) is now part of a
    # claim's access history.
    assert any(event["action"] == "claim_ingested" for event in events), events
    assert any(event["action"] == "finding_created" for event in events), events


def test_record_outcome_cross_tenant_lookup_is_404_against_real_rls(
    live_client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    """Phase 12: recording an outcome against another tenant's finding id
    must 404, never silently write against it -- same cross-tenant shape
    RLS already proves for GET /findings/{id}."""
    _, subject_a = _seed_tenant(app_session_factory, "outcome-a")
    _, subject_b = _seed_tenant(app_session_factory, "outcome-b")

    other_list = live_client.get("/findings", headers=_auth_headers(subject_b))
    other_finding_id = other_list.json()["items"][0]["id"]

    cross_tenant_response = live_client.post(
        f"/findings/{other_finding_id}/outcome",
        headers=_auth_headers(subject_a),
        json={"outcome": "recovered", "amount_recovered": "50.00"},
    )
    assert cross_tenant_response.status_code == 404

    own_list = live_client.get("/findings", headers=_auth_headers(subject_a))
    own_finding_id = own_list.json()["items"][0]["id"]
    own_response = live_client.post(
        f"/findings/{own_finding_id}/outcome",
        headers=_auth_headers(subject_a),
        json={"outcome": "recovered", "amount_recovered": "50.00"},
    )
    assert own_response.status_code == 200
    assert own_response.json()["outcome"] == "recovered"


def test_readyz_returns_200_against_real_postgres(live_client: TestClient) -> None:
    """Phase 9: /readyz has no auth and no tenant context -- it exists to
    prove the deployed instance can actually reach its database, which
    is exactly what this proves against a real one."""
    response = live_client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
