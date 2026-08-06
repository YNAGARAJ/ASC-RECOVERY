"""Phase 12 pilot demonstration, end to end against real Postgres: onboard
a tenant -> load its fee schedule -> ingest a synthetic multi-file "quarter"
of 835 remittances -> pull a findings report -> record outcomes -> confirm
a subsequent finding's confidence score reflects that history.

There is no real pilot customer in this environment (CLAUDE.md rule 1:
synthetic data only) -- "a quarter of 835 files" here is three synthetic
remittance files sharing one payer, procedure, and shortfall, honestly
labeled as a demonstration of the workflow, not a claim that a real pilot
happened.

Skips cleanly without TEST_DATABASE_URL, same pattern as every other
DB-backed test file in this repo (see tests/db/conftest.py). Written here,
never executed in this environment (no Postgres available) -- unverified
until CI's real-Postgres job runs it.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.repository import PostgresRepository
from db import repository as db_repository
from db.access import access_session
from db.base import make_engine, make_session_factory
from db.models import User as UserModel
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from packets.drafter import ScriptedPacketDrafter
from security.rbac import Role
from security.session import issue_session
from tests.db.conftest import seed_org_facility_user
from tests.domain.fixtures_x835 import (
    ELEMENT_SEP,
    SUB_ELEMENT_SEP,
    assemble,
    envelope_head,
    envelope_tail,
    plb_segment,
    seg,
)
from tests.ingestion.conftest import make_test_encryptor
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET = "test-only-secret-never-use-in-production"
_TEST_ENCRYPTOR = make_test_encryptor()


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/api/test_pilot_workflow_live_db.py "
            "needs a live Postgres 16 with migrations applied, connected as the "
            "asc_app role. See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    _require_database_url()
    url = OWNER_DATABASE_URL or TEST_DATABASE_URL
    assert url is not None
    return make_engine(url)


def _synthetic_claim_835(claim_control_number: str, payer_claim_control_number: str) -> str:
    """One claim shaped identically to tests.domain.fixtures_x835's
    `claim_segments` (charge 500, CO 60 total, PR 20) but with caller-
    supplied claim identifiers, so a synthetic "quarter" can ingest several
    distinct claims against the same payer without colliding on
    remittance file_hash dedup or claim identity."""
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            claim_control_number,
            "1",
            "500.00",
            "430.00",
            "20.00",
            "12",
            payer_claim_control_number,
            "11",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "QC",
            "1",
            "PATIENT SYNTHETIC",
            "TESTFIRST",
            "",
            "",
            "",
            "MI",
            f"TESTMBR{claim_control_number}",
        ),
        seg(
            ELEMENT_SEP,
            "NM1",
            "82",
            "2",
            "TEST RENDERING PROVIDER",
            "",
            "",
            "",
            "",
            "XX",
            "1999999999",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(ELEMENT_SEP, "DTM", "233", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "10.00"),
        seg(ELEMENT_SEP, "SVC", f"HC{SUB_ELEMENT_SEP}99213", "500.00", "430.00", "", "1"),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        seg(ELEMENT_SEP, "CAS", "CO", "45", "50.00"),
        seg(ELEMENT_SEP, "CAS", "PR", "1", "20.00"),
        seg(ELEMENT_SEP, "LQ", "HE", "N362"),
        plb_segment(),
        *envelope_tail(segment_count="17"),
    ]
    return assemble(segments)


def _onboard_tenant(
    owner_engine: Engine, session_factory: sessionmaker[Session]
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Mirrors scripts/onboard_customer.py's shape (create the org,
    facility, first admin user + membership, an initial contract +
    fee-schedule version) as a function rather than by shelling out to
    the script, so this test can inspect the resulting ids directly.
    Returns `(org_id, facility_id, subject)`."""
    user_id, org_id, facility_id = seed_org_facility_user(
        owner_engine, "Pilot ASC", role=Role.BILLER
    )

    with access_session(session_factory, user_id) as session:
        contract = db_repository.create_contract(
            session, org_id, TEST_PAYER, "Pilot payer contract"
        )
        db_repository.create_contract_version(
            session, org_id, contract.id, make_contract_version()
        )

    with session_factory() as session:
        row = session.get(UserModel, user_id)
        assert row is not None
        subject = row.subject

    return org_id, facility_id, subject


def _ingest_quarter(
    session_factory: sessionmaker[Session],
    user_id: uuid.UUID,
    facility_id: uuid.UUID,
    subject: str,
    count: int,
) -> None:
    with access_session(session_factory, user_id) as session:
        for i in range(count):
            content = _synthetic_claim_835(f"QCLAIM{i:04d}", f"QPAYERCTRL{i:04d}").encode("utf-8")
            outcome = ingest_file(
                session,
                facility_id,
                content=content,
                source="upload",
                uploaded_by=subject,
                scanner=EicarAwareScanner(),
                encryptor=_TEST_ENCRYPTOR,
            )
            assert outcome.status == "ingested"  # type: ignore[union-attr]


def _auth_headers(subject: str, org_id: uuid.UUID) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, str(org_id), mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_pilot_workflow_confidence_score_reflects_recorded_outcomes(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    org_id, facility_id, subject = _onboard_tenant(owner_engine, app_session_factory)

    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    user = repository.get_user_by_subject(subject)
    assert user is not None
    _ingest_quarter(app_session_factory, user.id, facility_id, subject, count=3)

    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)

    report = client.get("/findings", headers=_auth_headers(subject, org_id))
    assert report.status_code == 200
    assert report.json()["page"]["total"] == 3
    finding_ids = [item["id"] for item in report.json()["items"]]

    # Before any outcome is recorded, there's no decided history yet for
    # this payer/root_cause -- confidence is honestly None, never a
    # fabricated default.
    fresh_detail = client.get(
        f"/findings/{finding_ids[0]}", headers=_auth_headers(subject, org_id)
    )
    assert fresh_detail.status_code == 200
    assert fresh_detail.json()["confidence_score"] is None

    recovered = client.post(
        f"/findings/{finding_ids[0]}/outcome",
        headers=_auth_headers(subject, org_id),
        json={"outcome": "recovered", "amount_recovered": "50.00"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["outcome"] == "recovered"

    denied = client.post(
        f"/findings/{finding_ids[1]}/outcome",
        headers=_auth_headers(subject, org_id),
        json={"outcome": "denied"},
    )
    assert denied.status_code == 200
    assert denied.json()["outcome"] == "denied"

    # The third, still-undecided finding now has two historical outcomes
    # for the same payer/root_cause to learn from: one recovered, one
    # denied -- 1/2.
    third_detail = client.get(
        f"/findings/{finding_ids[2]}", headers=_auth_headers(subject, org_id)
    )
    assert third_detail.json()["confidence_score"] == "0.5"

    # The first finding's own confidence score excludes itself from its
    # own history -- only the second (denied) finding counts, so 0.
    first_detail = client.get(
        f"/findings/{finding_ids[0]}", headers=_auth_headers(subject, org_id)
    )
    assert first_detail.json()["confidence_score"] == "0"

    # Recording an outcome twice on the same finding is rejected, not
    # silently overwritten -- domain.outcomes.OutcomeAlreadyRecordedError.
    repeat = client.post(
        f"/findings/{finding_ids[0]}/outcome",
        headers=_auth_headers(subject, org_id),
        json={"outcome": "abandoned"},
    )
    assert repeat.status_code == 409
