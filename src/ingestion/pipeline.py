"""Top-level ingestion orchestrator -- the only place in ingestion/ doing DB
I/O directly. Wires: hash -> dedupe -> virus scan -> decode -> parse (pure,
domain.x835) -> fetch contract/prior-finding context -> plan (pure,
ingestion.plan) -> apply (ingestion.apply).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from opentelemetry.trace import NoOpTracer, Tracer
from sqlalchemy.orm import Session

from db import repository
from db.models import Finding as FindingModel
from domain.contract import ContractVersion
from domain.money import Money
from domain.x835 import ClaimStatus, parse_835
from ingestion.apply import ContractVersionIds, IngestionOutcome, apply_ingestion_plan
from ingestion.plan import PriorFinding, build_ingestion_plan, payer_key
from ingestion.virus_scan import VirusScanner
from observability.metrics import Instruments, noop_instruments, record_ingestion_outcome
from security.encryption import EnvelopeEncryptor


@dataclass(frozen=True, slots=True)
class DuplicateOutcome:
    remittance_id: uuid.UUID


def _quarantine_new_remittance(
    session: Session, tenant_id: uuid.UUID, remittance_id: uuid.UUID, *, actor: str, reason: str
) -> IngestionOutcome:
    repository.update_remittance_status(
        session, tenant_id, remittance_id, status="quarantined", quarantine_reason=reason
    )
    repository.write_audit_log(
        session,
        tenant_id,
        actor=actor,
        action="remittance_quarantined",
        resource_type="remittance",
        resource_id=str(remittance_id),
        phi_accessed=False,
    )
    return IngestionOutcome(
        remittance_id=remittance_id,
        status="quarantined",
        claims_created=0,
        findings_created=0,
        reconciliation_mismatches=0,
        dollars_detected=Decimal("0"),
    )


def ingest_file(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
    tracer: Tracer | None = None,
    instruments: Instruments | None = None,
) -> IngestionOutcome | DuplicateOutcome:
    """Thin tracing/metrics wrapper around `_ingest_file_impl` -- callers
    that don't pass `tracer`/`instruments` (every test written before
    Phase 8) get no-ops at negligible cost, so instrumentation is
    additive and never a required parameter that breaks them. `encryptor`
    is NOT optional/no-op-able the same way: there is no safe "no-op"
    encryptor, so every caller must supply a real one (see
    tests/ingestion/conftest.py's `make_test_encryptor` for the test-side
    default)."""
    resolved_tracer = tracer if tracer is not None else NoOpTracer()
    resolved_instruments = instruments if instruments is not None else noop_instruments()
    started = time.perf_counter()

    with resolved_tracer.start_as_current_span(
        "ingestion.ingest_file",
        attributes={"tenant_id": str(tenant_id), "source": source},
    ) as span:
        outcome = _ingest_file_impl(
            session,
            tenant_id,
            content=content,
            source=source,
            uploaded_by=uploaded_by,
            scanner=scanner,
            encryptor=encryptor,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if isinstance(outcome, IngestionOutcome):
            span.set_attribute("outcome_status", outcome.status)
            span.set_attribute("findings_created", outcome.findings_created)
            record_ingestion_outcome(
                resolved_instruments,
                tenant_id=str(tenant_id),
                status=outcome.status,
                latency_ms=latency_ms,
                dollars_detected=outcome.dollars_detected,
                findings_created=outcome.findings_created,
            )
        else:
            span.set_attribute("outcome_status", "duplicate")
        return outcome


def _ingest_file_impl(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
) -> IngestionOutcome | DuplicateOutcome:
    file_hash = hashlib.sha256(content).hexdigest()

    remittance, is_new = repository.record_remittance_if_new(
        session, tenant_id, file_hash, source=source, uploaded_by=uploaded_by
    )
    if not is_new:
        return DuplicateOutcome(remittance_id=remittance.id)

    scan_result = scanner.scan(content)
    if not scan_result.clean:
        return _quarantine_new_remittance(
            session,
            tenant_id,
            remittance.id,
            actor=uploaded_by,
            reason=f"virus scan flagged this file: {scan_result.detail}",
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _quarantine_new_remittance(
            session,
            tenant_id,
            remittance.id,
            actor=uploaded_by,
            reason=f"could not decode file as UTF-8: {exc}",
        )

    parse_result = parse_835(text)

    payer_ids = {payer_key(txn.payer) for txn in parse_result.transactions}
    contract_versions_by_payer: dict[str, tuple[ContractVersion, ...]] = {}
    contract_version_ids: dict[tuple[str, date], uuid.UUID] = {}
    for pid in payer_ids:
        rows = repository.list_contract_versions(session, tenant_id, pid)
        contract_versions_by_payer[pid] = tuple(version for _, version in rows)
        for version_id, version in rows:
            contract_version_ids[(version.payer_id, version.effective_from)] = version_id

    reversal_control_numbers = {
        claim.payer_claim_control_number
        for txn in parse_result.transactions
        for claim in txn.claims
        if claim.status is ClaimStatus.REVERSAL_OF_PREVIOUS_PAYMENT
    }
    prior_findings_by_control_number: dict[str, tuple[PriorFinding, ...]] = {
        control_number: tuple(
            _to_prior_finding(row)
            for row in repository.list_findings_by_payer_claim_control_number(
                session, tenant_id, control_number
            )
        )
        for control_number in reversal_control_numbers
    }

    plan = build_ingestion_plan(
        parse_result,
        contract_versions_by_payer=contract_versions_by_payer,
        prior_findings_by_control_number=prior_findings_by_control_number,
    )

    contract_version_ids_typed: ContractVersionIds = contract_version_ids
    return apply_ingestion_plan(
        session,
        tenant_id,
        plan,
        remittance_id=remittance.id,
        actor=uploaded_by,
        contract_version_ids=contract_version_ids_typed,
        encryptor=encryptor,
    )


def _to_prior_finding(row: FindingModel) -> PriorFinding:
    return PriorFinding(
        line_index=row.line_index,
        procedure_code=row.procedure_code,
        expected_allowed=None if row.expected_allowed is None else Money(row.expected_allowed),
        actual_allowed=Money(row.actual_allowed),
        shortfall=Money(row.shortfall),
        root_cause=row.root_cause,
    )
