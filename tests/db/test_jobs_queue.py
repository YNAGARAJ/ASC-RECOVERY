"""DB-backed proofs for the Phase 7 job-queue primitives in
`db.repository`'s "Jobs" section: enqueue/dedup, the API-facing surface
(get/list/cancel, RLS-scoped), and the worker-facing surface (claim,
progress, cancel-check, complete, fail/backoff/dead-letter,
cancel-as-cancelled -- all owner-privileged, per that section's own
module docstring). Requires a live Postgres -- see docs/DB_SETUP.md.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from tests.db.conftest import seed_org_facility_user

_STALE_LOCK_AFTER = timedelta(minutes=10)


def _enqueue(
    owner_engine: Engine,
    facility_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    dedup_key: str | None = None,
    job_type: str = "ingest_remittance",
) -> tuple[uuid.UUID, bool]:
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        row, is_new = repository.enqueue_job(
            session,
            facility_id,
            job_type=job_type,
            dedup_key=dedup_key,
            payload_encrypted=None,
            user_id=user_id,
            actor="tester",
        )
        return row.id, is_new


def test_enqueue_is_idempotent_on_facility_job_type_dedup_key(
    owner_engine: Engine,
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Enqueue dedup tenant")
    key = uuid.uuid4().hex

    job_id_1, is_new_1 = _enqueue(owner_engine, facility_id, user_id, dedup_key=key)
    job_id_2, is_new_2 = _enqueue(owner_engine, facility_id, user_id, dedup_key=key)

    assert is_new_1 is True
    assert is_new_2 is False
    assert job_id_1 == job_id_2


def test_enqueue_with_no_dedup_key_never_conflicts(owner_engine: Engine) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Enqueue no-dedup tenant")

    job_id_1, is_new_1 = _enqueue(owner_engine, facility_id, user_id, dedup_key=None)
    job_id_2, is_new_2 = _enqueue(owner_engine, facility_id, user_id, dedup_key=None)

    assert is_new_1 is True
    assert is_new_2 is True
    assert job_id_1 != job_id_2


def test_owner_can_get_and_list_their_own_facilitys_job(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Job read tenant")
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    with access_session(app_session_factory, user_id) as session:
        row = repository.get_job(session, facility_id, job_id)
        assert row is not None
        assert row.id == job_id
        assert row.status == "queued"

        rows, total = repository.list_jobs(session, facility_id)
        assert total == 1
        assert rows[0].id == job_id


def test_outsider_cannot_get_list_or_cancel_another_facilitys_job(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    owner_id, _org_a, facility_a = seed_org_facility_user(owner_engine, "Job RLS tenant A")
    outsider_id, _org_b, _facility_b = seed_org_facility_user(owner_engine, "Job RLS tenant B")
    job_id, _ = _enqueue(owner_engine, facility_a, owner_id)

    with access_session(app_session_factory, outsider_id) as session:
        assert repository.get_job(session, facility_a, job_id) is None
        rows, total = repository.list_jobs(session, facility_a)
        assert rows == []
        assert total == 0
        assert repository.cancel_job(session, facility_a, job_id) is False

    # Prove it's RLS narrowing the query, not the job simply not existing --
    # the owning tenant can still see and cancel it.
    with access_session(app_session_factory, owner_id) as session:
        assert repository.get_job(session, facility_a, job_id) is not None
        assert repository.cancel_job(session, facility_a, job_id) is True


def test_cancel_job_is_a_no_op_once_terminal(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(
        owner_engine, "Job terminal-cancel tenant"
    )
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        repository.complete_job(session, job_id, result={"status": "ingested"})

    with access_session(app_session_factory, user_id) as session:
        assert repository.cancel_job(session, facility_id, job_id) is False


def test_claim_next_job_marks_running_and_increments_attempts(owner_engine: Engine) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Job claim tenant")
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        claimed = repository.claim_next_job(
            session, worker_id="worker-a", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=3
        )
        assert claimed is not None
        assert claimed.id == job_id
        assert claimed.status == "running"
        assert claimed.locked_by == "worker-a"
        assert claimed.attempts == 1

    # Already running -- a second worker polling finds nothing due.
    with session_factory() as session, session.begin():
        nothing = repository.claim_next_job(
            session, worker_id="worker-b", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=3
        )
        assert nothing is None


def test_claim_next_job_reclaims_a_stale_lock(owner_engine: Engine) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Job stale-lock tenant")
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        first = repository.claim_next_job(
            session, worker_id="worker-a", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=3
        )
        assert first is not None

    # A killed worker's lock is older than stale_lock_after=0 -- immediately
    # reclaimable by a different worker, same row.
    with session_factory() as session, session.begin():
        reclaimed = repository.claim_next_job(
            session, worker_id="worker-b", stale_lock_after=timedelta(seconds=0), per_org_limit=3
        )
        assert reclaimed is not None
        assert reclaimed.id == job_id
        assert reclaimed.locked_by == "worker-b"
        assert reclaimed.attempts == 2


def test_progress_and_cancel_check_are_worker_facing_and_owner_scoped(
    owner_engine: Engine,
) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Job progress tenant")
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        assert repository.is_cancel_requested(session, job_id) is False
        repository.update_job_progress(session, job_id, percent=42, message="17/40 claims")

    with session_factory() as session, session.begin():
        row = repository.get_job(session, facility_id, job_id)
        assert row is not None
        assert row.progress_percent == 42
        assert row.progress_message == "17/40 claims"


def test_fail_job_backs_off_then_dead_letters_after_max_attempts(owner_engine: Engine) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Job dead-letter tenant")
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        row, _ = repository.enqueue_job(
            session,
            facility_id,
            job_type="ingest_remittance",
            dedup_key=None,
            payload_encrypted=None,
            user_id=user_id,
            actor="tester",
            max_attempts=2,
        )
        job_id = row.id

    for attempt in (1, 2):
        with session_factory() as session, session.begin():
            claimed = repository.claim_next_job(
                session,
                worker_id=f"worker-{attempt}",
                stale_lock_after=_STALE_LOCK_AFTER,
                per_org_limit=3,
            )
            assert claimed is not None
            assert claimed.attempts == attempt

        with session_factory() as session, session.begin():
            updated = repository.fail_job(
                session, job_id, error="synthetic failure", backoff_seconds=0.0
            )
            if attempt < 2:
                assert updated.status == "failed"
                assert updated.next_run_at is not None
            else:
                assert updated.status == "dead_letter"
                assert updated.completed_at is not None


def test_cancel_job_as_cancelled_is_terminal(owner_engine: Engine) -> None:
    user_id, _org_id, facility_id = seed_org_facility_user(
        owner_engine, "Job cancel-as-cancelled tenant"
    )
    job_id, _ = _enqueue(owner_engine, facility_id, user_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        claimed = repository.claim_next_job(
            session, worker_id="worker-a", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=3
        )
        assert claimed is not None

    with session_factory() as session, session.begin():
        repository.cancel_job_as_cancelled(session, job_id)

    with session_factory() as session, session.begin():
        row = repository.get_job(session, facility_id, job_id)
        assert row is not None
        assert row.status == "cancelled"
        assert row.completed_at is not None


def test_per_org_concurrency_limit_is_org_wide_not_per_facility(owner_engine: Engine) -> None:
    """Two facilities under the *same* org, one queued job each,
    `per_org_limit=1` -- proves the ceiling counts running jobs by
    `facilities.org_id`, not by `facility_id`: claiming the second job is
    refused while the first is still running, even though it belongs to a
    different facility."""
    user_id, org_id, facility_a = seed_org_facility_user(owner_engine, "Org concurrency tenant")
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        facility_b = repository.create_facility(session, org_id, name="Second facility").id

    job_a_id, _ = _enqueue(owner_engine, facility_a, user_id, dedup_key="a")
    job_b_id, _ = _enqueue(owner_engine, facility_b, user_id, dedup_key="b")

    with session_factory() as session, session.begin():
        first_claim = repository.claim_next_job(
            session, worker_id="worker-a", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=1
        )
        assert first_claim is not None
        assert first_claim.id in (job_a_id, job_b_id)

    # The org already has one job `running` -- the other facility's queued
    # job is refused, not because of its own facility, but the org total.
    with session_factory() as session, session.begin():
        second_claim = repository.claim_next_job(
            session, worker_id="worker-b", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=1
        )
        assert second_claim is None

    # Once the running job leaves 'running', the org's other job becomes
    # claimable again -- proves the count is live, not a one-time gate.
    with session_factory() as session, session.begin():
        repository.complete_job(session, first_claim.id, result={"status": "ingested"})

    with session_factory() as session, session.begin():
        third_claim = repository.claim_next_job(
            session, worker_id="worker-c", stale_lock_after=_STALE_LOCK_AFTER, per_org_limit=1
        )
        assert third_claim is not None
        assert third_claim.id != first_claim.id
