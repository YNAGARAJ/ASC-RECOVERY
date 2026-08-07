"""DB-backed proofs for src/jobs/runner.py -- the actual Phase 7 gate
(`docs/MASTER-BUILD-PROMPT-V2.md`): enqueue via the real HTTP path, run it
with the real worker core (`claim_and_run_once`), observe the outcome via
the real HTTP path again, and prove a claim abandoned by a dead worker gets
reclaimed and completes without duplicating any data. Also carries the
ingestion-failure-rate alert's wiring proof, moved here from
tests/api/test_alerts_live_db.py now that ingestion runs inside a job, not
inside PostgresRepository (see that file's own docstring).

Skips cleanly without `TEST_DATABASE_URL`, same pattern as every other
DB-backed test file in this repo. Written here, never executed in this
environment (no Docker/WSL/Postgres available) -- honest about being
unverified, same as the rest of this repo's DB-backed test files.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.repository import PostgresRepository
from db import repository as db_repository
from db.access import access_session
from db.models import Claim as ClaimModel
from db.models import Remittance as RemittanceModel
from db.models import User as UserModel
from ingestion.virus_scan import EicarAwareScanner
from jobs.runner import claim_and_run_once
from observability.alerts import Alert
from packets.drafter import ScriptedPacketDrafter
from security.rbac import Role
from security.session import issue_session
from tests.db.conftest import seed_org_facility_user
from tests.domain.fixtures_x835 import malformed_missing_isa, minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

JWT_SECRET = "test-only-secret-never-use-in-production"
_TEST_ENCRYPTOR = make_test_encryptor()


@dataclass
class _FakeNotificationPort:
    alerts: list[Alert] = field(default_factory=list)

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _seed_tenant(
    owner_engine: Engine, session_factory: sessionmaker[Session], label: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (user_id, org_id, facility_id), with one open-ended
    fee-schedule contract already in place so an ingested claim actually
    prices (and can be found underpaid)."""
    user_id, org_id, facility_id = seed_org_facility_user(owner_engine, label, role=Role.BILLER)
    with access_session(session_factory, user_id) as session:
        contract = db_repository.create_contract(session, org_id, TEST_PAYER, f"{label} contract")
        db_repository.create_contract_version(
            session, org_id, contract.id, make_contract_version()
        )
    return user_id, org_id, facility_id


def _auth_headers(subject: str, org_id: uuid.UUID) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, str(org_id), mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_upload_is_enqueued_claimed_by_the_worker_and_observable_via_the_api(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
    owner_session_factory: sessionmaker[Session],
) -> None:
    user_id, org_id, facility_id = _seed_tenant(
        owner_engine, app_session_factory, "job gate happy path"
    )
    with app_session_factory() as session:
        row = session.get(UserModel, user_id)
        assert row is not None
        subject = row.subject

    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)

    upload = client.post(
        "/remittances",
        headers=_auth_headers(subject, org_id),
        files={
            "file": (
                "remit.835",
                minimal_valid_835().encode("utf-8"),
                "application/octet-stream",
            )
        },
    )
    assert upload.status_code == 202
    body = upload.json()
    assert body["status"] == "queued"
    job_id = uuid.UUID(body["id"])

    did_work = claim_and_run_once(
        owner_session_factory,
        app_session_factory,
        worker_id="test-worker",
        encryptor=_TEST_ENCRYPTOR,
        scanner=EicarAwareScanner(),
    )
    assert did_work is True

    result = client.get(f"/jobs/{job_id}", headers=_auth_headers(subject, org_id))
    assert result.status_code == 200
    payload = result.json()
    assert payload["status"] == "succeeded"
    assert payload["progress_percent"] == 100
    assert payload["result"]["status"] == "ingested"
    assert payload["result"]["claims_created"] == 1
    assert payload["result"]["findings_created"] == 1
    # Never leaked, even though it's a Text column right there on the row.
    assert "payload_encrypted" not in payload
    assert "payload" not in payload


def test_a_claim_abandoned_by_a_dead_worker_is_reclaimed_and_completes_without_duplicating(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
    owner_session_factory: sessionmaker[Session],
) -> None:
    """Simulates a worker that claimed a job and then died before doing any
    work at all -- the row is left `status='running'`, `locked_by`
    pointing at a process that no longer exists. `claim_and_run_once`
    with `stale_lock_after=0` treats that lock as immediately reclaimable
    (same as `db.repository.claim_next_job`'s own real staleness check,
    just not waiting out the real 10-minute default) -- a second worker
    picks the row back up, actually runs it, and it succeeds with exactly
    one remittance/claim, never two, even though the row was claimed
    twice."""
    user_id, _org_id, facility_id = _seed_tenant(
        owner_engine, app_session_factory, "job gate stale lock"
    )
    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    job = repository.enqueue_remittance_ingestion(
        user_id,
        facility_id,
        content=minimal_valid_835().encode("utf-8"),
        source="upload",
        uploaded_by="stale-lock-tester",
    )

    # The "dead worker": claims the row, does nothing else.
    with owner_session_factory() as session, session.begin():
        dead_worker_claim = db_repository.claim_next_job(
            session,
            worker_id="dead-worker",
            stale_lock_after=timedelta(minutes=10),
            per_org_limit=3,
        )
    assert dead_worker_claim is not None
    assert dead_worker_claim.id == job.id
    assert dead_worker_claim.attempts == 1

    # A real worker, immediately -- stale_lock_after=0 means "even a
    # lock acquired a moment ago is reclaimable," standing in for having
    # actually waited out the real staleness window.
    did_work = claim_and_run_once(
        owner_session_factory,
        app_session_factory,
        worker_id="live-worker",
        encryptor=_TEST_ENCRYPTOR,
        scanner=EicarAwareScanner(),
        stale_lock_after=timedelta(seconds=0),
    )
    assert did_work is True

    with owner_session_factory() as session, session.begin():
        final = db_repository.get_job(session, facility_id, job.id)
        assert final is not None
        assert final.status == "succeeded"
        assert final.attempts == 2  # genuinely reclaimed, not a fresh first attempt
        assert final.locked_by == "live-worker"

        remittance_count = session.execute(
            select(func.count()).select_from(RemittanceModel)
        ).scalar_one()
        claim_count = session.execute(
            select(func.count())
            .select_from(ClaimModel)
            .where(ClaimModel.facility_id == facility_id)
        ).scalar_one()
    assert remittance_count == 1
    assert claim_count == 1


def test_high_quarantine_rate_fires_an_ingestion_failure_alert_via_the_worker(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
    owner_session_factory: sessionmaker[Session],
) -> None:
    user_id, _org_id, facility_id = _seed_tenant(
        owner_engine, app_session_factory, "job gate quarantine alert"
    )
    notifier = _FakeNotificationPort()
    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )

    # One clean ingestion, then two quarantined ones (distinct content each
    # time -- identical content would dedupe at the job's own dedup_key,
    # never reaching the worker a second time at all). 2/3 quarantined is
    # well above the 10% default rate.
    good = repository.enqueue_remittance_ingestion(
        user_id,
        facility_id,
        content=minimal_valid_835().encode("utf-8"),
        source="upload",
        uploaded_by="quarantine-alert-tester",
    )
    bad_jobs = [
        repository.enqueue_remittance_ingestion(
            user_id,
            facility_id,
            content=f"{malformed_missing_isa()} seq={i}".encode(),
            source="upload",
            uploaded_by="quarantine-alert-tester",
        )
        for i in range(2)
    ]

    for _ in range(3):
        did_work = claim_and_run_once(
            owner_session_factory,
            app_session_factory,
            worker_id="quarantine-alert-worker",
            encryptor=_TEST_ENCRYPTOR,
            scanner=EicarAwareScanner(),
            notifier=notifier,
        )
        assert did_work is True

    with owner_session_factory() as session, session.begin():
        good_row = db_repository.get_job(session, facility_id, good.id)
        assert good_row is not None
        assert good_row.status == "succeeded"
        for bad_job in bad_jobs:
            bad_row = db_repository.get_job(session, facility_id, bad_job.id)
            assert bad_row is not None
            assert bad_row.status == "succeeded"
            assert bad_row.result is not None
            assert bad_row.result["status"] == "quarantined"

    failure_alerts = [a for a in notifier.alerts if a.name == "ingestion_failure_rate"]
    assert len(failure_alerts) >= 1
