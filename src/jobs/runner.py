"""Worker-side job execution (Phase 7, `docs/MASTER-BUILD-PROMPT-V2.md`).

`claim_and_run_once` is the testable core -- claims at most one due job
and runs it to completion, cancellation, or failure, returning whether it
did work. `src/worker.py`'s CLI loop is a thin `while True: ...` wrapper
around this, the same split `scripts/ingestion/poll_remittances.py`
already uses between its testable core and its own CLI loop.

**Two session factories, not one** -- see `db.repository`'s "Jobs"
section docstring for the full reasoning: `owner_session_factory`
(`BYPASSRLS`) drives the queue itself (claim/progress/cancel-check/
complete/fail -- system-wide across every facility, not any one user's
resolved access); `app_session_factory` (ordinary `asc_app`) re-
establishes the *job's own submitter's* resolved access, via
`access_session`, to actually run the job's business logic. This is what
makes "jobs carry the access context so a worker cannot read outside its
facility scope" true: the worker process can see the whole queue, but
the data any single job touches is still exactly what `job.user_id`
could reach, re-checked fresh by RLS at execution time.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

from opentelemetry.trace import Tracer
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.models import Job as JobModel
from ingestion.apply import IngestionOutcome, JobCancelledError
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.virus_scan import VirusScanner
from jobs.payload import parse_ingestion_payload
from observability.alert_state import IngestionOutcomeTracker
from observability.alerts import evaluate_ingestion_failure_alert, evaluate_job_dead_lettered_alert
from observability.metrics import Instruments
from observability.notifications import NotificationPort
from security.encryption import EnvelopeEncryptor
from security.phi_columns import decrypt_phi_field
from security.redaction import scrub_text

_DEFAULT_STALE_LOCK_AFTER = timedelta(minutes=10)
_DEFAULT_PER_ORG_LIMIT = 3
_BACKOFF_BASE_SECONDS = 30.0
_BACKOFF_CAP_SECONDS = 3600.0

# Per-process, in-memory, same "no live effect until something calls
# .record()" contract api.repository.PostgresRepository's own instance
# used to have before ingestion moved into a job (F-11,
# docs/audit/REGISTER.md) -- this is the worker-side home for that same
# tracker now that the outcome it feeds on only ever exists here.
_ingestion_alert_tracker = IngestionOutcomeTracker()

JobHandler = Callable[
    [JobModel, sessionmaker[Session], sessionmaker[Session], EnvelopeEncryptor, VirusScanner,
     Tracer | None, Instruments | None, NotificationPort | None],
    None,
]


def _backoff_seconds(attempts: int) -> float:
    """Exponential, capped -- `attempts` is already post-increment (the
    attempt that just ran), so the first failure (`attempts=1`) waits the
    base delay, not zero."""
    delay = _BACKOFF_BASE_SECONDS * float(2 ** max(attempts - 1, 0))
    return min(delay, _BACKOFF_CAP_SECONDS)


def run_ingestion_job(
    job: JobModel,
    app_session_factory: sessionmaker[Session],
    owner_session_factory: sessionmaker[Session],
    encryptor: EnvelopeEncryptor,
    scanner: VirusScanner,
    tracer: Tracer | None,
    instruments: Instruments | None,
    notifier: NotificationPort | None,
) -> None:
    if job.payload_encrypted is None:
        raise ValueError(f"job {job.id} has no payload")
    decrypted = decrypt_phi_field(encryptor, job.payload_encrypted)
    if decrypted is None:
        raise ValueError(f"job {job.id} payload decrypted to nothing")
    content, source = parse_ingestion_payload(decrypted)

    def on_progress(processed: int, total: int) -> None:
        percent = int(processed / total * 100) if total else 100
        with owner_session_factory() as session, session.begin():
            repository.update_job_progress(
                session, job.id, percent=percent, message=f"{processed}/{total} claims processed"
            )

    def should_cancel() -> bool:
        with owner_session_factory() as session, session.begin():
            return repository.is_cancel_requested(session, job.id)

    with access_session(app_session_factory, job.user_id) as session:
        outcome = ingest_file(
            session,
            job.facility_id,
            content=content,
            source=source,
            uploaded_by=job.actor,
            scanner=scanner,
            encryptor=encryptor,
            tracer=tracer,
            instruments=instruments,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        result: dict[str, object] = (
            {"status": "duplicate", "remittance_id": str(outcome.remittance_id)}
            if isinstance(outcome, DuplicateOutcome)
            else {
                "status": outcome.status,
                "remittance_id": str(outcome.remittance_id),
                "claims_created": outcome.claims_created,
                "findings_created": outcome.findings_created,
                "reconciliation_mismatches": outcome.reconciliation_mismatches,
                "dollars_detected": str(outcome.dollars_detected),
            }
        )
        repository.write_audit_log(
            session,
            job.facility_id,
            actor=job.actor,
            action="job_completed",
            resource_type="job",
            resource_id=str(job.id),
            phi_accessed=isinstance(outcome, IngestionOutcome),
        )

    # F-11 (docs/audit/REGISTER.md): pure in-memory bookkeeping, not a DB
    # write, so it belongs outside the transaction above -- same
    # reasoning api.repository.PostgresRepository.ingest_remittance's own
    # version of this check used before ingestion moved into a job.
    # DuplicateOutcome is excluded on purpose: a duplicate upload was
    # never actually (re)processed, so it shouldn't count toward either
    # side of the quarantine ratio.
    if isinstance(outcome, IngestionOutcome) and notifier is not None:
        quarantined_count, total_count = _ingestion_alert_tracker.record(
            str(job.facility_id), quarantined=(outcome.status == "quarantined")
        )
        alert = evaluate_ingestion_failure_alert(
            quarantined_count=quarantined_count, total_count=total_count
        )
        if alert is not None:
            notifier.notify(alert)

    with owner_session_factory() as session, session.begin():
        repository.complete_job(session, job.id, result=result)


_HANDLERS: dict[str, JobHandler] = {"ingest_remittance": run_ingestion_job}


def _fail(
    owner_session_factory: sessionmaker[Session],
    job: JobModel,
    *,
    error: str,
    notifier: NotificationPort | None,
) -> None:
    with owner_session_factory() as session, session.begin():
        updated = repository.fail_job(
            session, job.id, error=scrub_text(error), backoff_seconds=_backoff_seconds(job.attempts)
        )
        new_status = updated.status
        if new_status == "dead_letter":
            repository.write_audit_log(
                session,
                job.facility_id,
                actor=job.actor,
                action="job_dead_lettered",
                resource_type="job",
                resource_id=str(job.id),
                phi_accessed=False,
            )
    if new_status == "dead_letter" and notifier is not None:
        notifier.notify(
            evaluate_job_dead_lettered_alert(
                job_id=str(job.id),
                job_type=job.job_type,
                facility_id=str(job.facility_id),
                attempts=job.attempts,
            )
        )


def claim_and_run_once(
    owner_session_factory: sessionmaker[Session],
    app_session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    encryptor: EnvelopeEncryptor,
    scanner: VirusScanner,
    tracer: Tracer | None = None,
    instruments: Instruments | None = None,
    notifier: NotificationPort | None = None,
    stale_lock_after: timedelta = _DEFAULT_STALE_LOCK_AFTER,
    per_org_limit: int = _DEFAULT_PER_ORG_LIMIT,
) -> bool:
    """Returns `True` if a job was claimed (regardless of how it turned
    out), `False` if the queue had nothing due -- `src/worker.py`'s loop
    uses this to decide whether to sleep before polling again."""
    with owner_session_factory() as owner_session, owner_session.begin():
        job = repository.claim_next_job(
            owner_session,
            worker_id=worker_id,
            stale_lock_after=stale_lock_after,
            per_org_limit=per_org_limit,
        )
    if job is None:
        return False
    if instruments is not None:
        instruments.queue_depth.add(-1)

    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        _fail(
            owner_session_factory,
            job,
            error=f"no handler registered for job_type {job.job_type!r}",
            notifier=notifier,
        )
        return True

    try:
        handler(
            job,
            app_session_factory,
            owner_session_factory,
            encryptor,
            scanner,
            tracer,
            instruments,
            notifier,
        )
    except JobCancelledError:
        with owner_session_factory() as session, session.begin():
            repository.cancel_job_as_cancelled(session, job.id)
    except Exception as exc:  # noqa: BLE001 -- one job's failure must never crash the worker loop
        _fail(owner_session_factory, job, error=str(exc), notifier=notifier)
    return True


def next_worker_id() -> str:
    """A fresh, unique-enough identity per worker process -- only used to
    populate `jobs.locked_by` for operator debugging (`docs/RUNBOOK.md`),
    never compared against anything, so a random uuid is sufficient."""
    return f"worker-{uuid.uuid4().hex[:12]}"
