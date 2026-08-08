"""Top-level ingestion orchestrator -- the only place in ingestion/ doing DB
I/O directly. Wires: hash -> dedupe -> virus scan -> decode -> parse (pure,
domain.x835) -> fetch contract/prior-finding context -> plan (pure,
ingestion.plan) -> apply (ingestion.apply).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from opentelemetry.trace import NoOpTracer, Tracer
from sqlalchemy.orm import Session

from db import repository
from db.models import Finding as FindingModel
from domain.contract import ContractVersion
from domain.money import Money
from domain.x835 import (
    ISA_ELEMENT_SEP_INDEX,
    ISA_MIN_LENGTH,
    ISA_TERMINATOR_INDEX,
    ClaimStatus,
    parse_835,
)
from domain.x837 import parse_837
from ingestion.apply import ContractVersionIds, IngestionOutcome, apply_ingestion_plan
from ingestion.plan import PriorFinding, build_ingestion_plan, payer_key
from ingestion.virus_scan import VirusScanner
from observability.metrics import Instruments, noop_instruments, record_ingestion_outcome
from security.encryption import EnvelopeEncryptor
from security.phi_columns import encrypt_phi_field


@dataclass(frozen=True, slots=True)
class DuplicateOutcome:
    remittance_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DuplicateClaimFileOutcome:
    claim_file_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ClaimFileOutcome:
    """Phase 9 (`docs/MASTER-BUILD-PROMPT-V2.md`): the outcome of
    ingesting an 837 claim file -- deliberately not `IngestionOutcome`
    (no claims/findings are ever *created* by an 837, only enriched;
    `remittance_id` would be a category error, there's no remittance
    involved at all). `claims_enriched`/`claims_unmatched` mirror
    `IngestionOutcome`'s "never silently invisible" principle -- an 837
    claim that couldn't be matched to an existing 835-created claim is
    counted, not dropped without a trace."""

    claim_file_id: uuid.UUID
    status: str
    claims_enriched: int
    claims_unmatched: int
    quarantine_reason: str | None = None


def _quarantine_new_remittance(
    session: Session, facility_id: uuid.UUID, remittance_id: uuid.UUID, *, actor: str, reason: str
) -> IngestionOutcome:
    repository.update_remittance_status(
        session, facility_id, remittance_id, status="quarantined", quarantine_reason=reason
    )
    repository.write_audit_log(
        session,
        facility_id,
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
    facility_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
    tracer: Tracer | None = None,
    instruments: Instruments | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> IngestionOutcome | DuplicateOutcome | ClaimFileOutcome | DuplicateClaimFileOutcome:
    """Thin tracing/metrics wrapper around `_ingest_file_impl` -- callers
    that don't pass `tracer`/`instruments` (every test written before
    Phase 8) get no-ops at negligible cost, so instrumentation is
    additive and never a required parameter that breaks them. `encryptor`
    is NOT optional/no-op-able the same way: there is no safe "no-op"
    encryptor, so every caller must supply a real one (see
    tests/ingestion/conftest.py's `make_test_encryptor` for the test-side
    default). `on_progress`/`should_cancel` (Phase 7, `src/jobs/runner.py`)
    are passed straight through to `ingestion.apply.apply_ingestion_plan`,
    whose own docstring covers them -- both default to `None`/no-op;
    unused on the 837 path (Phase 9), which has no per-line loop long
    enough to need either. `_ingest_file_impl` dispatches to the 835 or
    837 path by peeking at the transaction-set-identifier element before
    either parser runs -- see `_detect_transaction_set`."""
    resolved_tracer = tracer if tracer is not None else NoOpTracer()
    resolved_instruments = instruments if instruments is not None else noop_instruments()
    started = time.perf_counter()

    with resolved_tracer.start_as_current_span(
        "ingestion.ingest_file",
        attributes={"facility_id": str(facility_id), "source": source},
    ) as span:
        outcome = _ingest_file_impl(
            session,
            facility_id,
            content=content,
            source=source,
            uploaded_by=uploaded_by,
            scanner=scanner,
            encryptor=encryptor,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if isinstance(outcome, IngestionOutcome):
            span.set_attribute("outcome_status", outcome.status)
            span.set_attribute("findings_created", outcome.findings_created)
            record_ingestion_outcome(
                resolved_instruments,
                facility_id=str(facility_id),
                status=outcome.status,
                latency_ms=latency_ms,
                dollars_detected=outcome.dollars_detected,
                findings_created=outcome.findings_created,
            )
        elif isinstance(outcome, ClaimFileOutcome):
            span.set_attribute("outcome_status", outcome.status)
            span.set_attribute("claims_enriched", outcome.claims_enriched)
            span.set_attribute("claims_unmatched", outcome.claims_unmatched)
        else:
            span.set_attribute("outcome_status", "duplicate")
        return outcome


def _detect_transaction_set(content: bytes) -> str | None:
    """Peeks at the ST segment's own transaction-set-identifier element
    (ST01: `"835"` or `"837"`) directly off the raw bytes, before any
    dedup/virus-scan/table-specific handling -- both the 835 and 837
    paths need this decided before they know which table (`remittances`
    vs `claim_files`) tracks this file's idempotency/quarantine status.
    Reads the ISA header for delimiters the same fixed-width way
    `domain.x835.parse_835`/`domain.x837.parse_837` each independently
    do once they actually run. Returns `None` for anything that doesn't
    even look like a parseable X12 envelope (missing/short ISA, bad
    UTF-8, no ST segment) -- callers treat `None` and anything other
    than the literal `"837"` identically: fall through to the existing
    835 path, whose own parser/quarantine handling already covers a
    genuinely malformed file exactly as it did before this function
    existed. This function's only job is a narrow, additive "is this
    specifically an 837" check, never a replacement for 835's own error
    handling."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) < ISA_MIN_LENGTH or not text.startswith("ISA"):
        return None
    element_sep = text[ISA_ELEMENT_SEP_INDEX]
    terminator = text[ISA_TERMINATOR_INDEX]
    for chunk in text[ISA_MIN_LENGTH:].split(terminator):
        chunk = chunk.strip("\r\n")
        if not chunk:
            continue
        elements = chunk.split(element_sep)
        if elements[0] == "ST":
            return elements[1] if len(elements) > 1 else None
    return None


def _ingest_file_impl(
    session: Session,
    facility_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> IngestionOutcome | DuplicateOutcome | ClaimFileOutcome | DuplicateClaimFileOutcome:
    if _detect_transaction_set(content) == "837":
        return _ingest_837_impl(
            session,
            facility_id,
            content=content,
            source=source,
            uploaded_by=uploaded_by,
            scanner=scanner,
            encryptor=encryptor,
        )
    return _ingest_835_impl(
        session,
        facility_id,
        content=content,
        source=source,
        uploaded_by=uploaded_by,
        scanner=scanner,
        encryptor=encryptor,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )


def _ingest_835_impl(
    session: Session,
    facility_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> IngestionOutcome | DuplicateOutcome:
    file_hash = hashlib.sha256(content).hexdigest()

    remittance, is_new = repository.record_remittance_if_new(
        session, facility_id, file_hash, source=source, uploaded_by=uploaded_by
    )
    if not is_new:
        return DuplicateOutcome(remittance_id=remittance.id)

    scan_result = scanner.scan(content)
    if not scan_result.clean:
        return _quarantine_new_remittance(
            session,
            facility_id,
            remittance.id,
            actor=uploaded_by,
            reason=f"virus scan flagged this file: {scan_result.detail}",
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _quarantine_new_remittance(
            session,
            facility_id,
            remittance.id,
            actor=uploaded_by,
            reason=f"could not decode file as UTF-8: {exc}",
        )

    parse_result = parse_835(text)

    # Contracts are org-scoped, not facility-scoped (db/models.py's module
    # docstring) -- one ASC_GROUP's facilities share a payer rate card.
    # Ingestion only ever knows the target facility, so resolve its
    # parent org once here for the contract-version lookups below, and
    # for Phase 6's per-org encryption key (same org_id, one extra cheap
    # lookup, not worth threading a second facility->org resolution).
    org_id = repository.get_org_id_for_facility(session, facility_id)
    org_kms_key_id = (
        repository.get_organization_kms_key_id(session, org_id) if org_id is not None else None
    )

    payer_ids = {payer_key(txn.payer) for txn in parse_result.transactions}
    contract_versions_by_payer: dict[str, tuple[ContractVersion, ...]] = {}
    contract_version_ids: dict[tuple[str, date], uuid.UUID] = {}
    if org_id is not None:
        for pid in payer_ids:
            rows = repository.list_contract_versions(session, org_id, pid)
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
                session, facility_id, control_number
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
        facility_id,
        plan,
        remittance_id=remittance.id,
        actor=uploaded_by,
        contract_version_ids=contract_version_ids_typed,
        encryptor=encryptor,
        org_kms_key_id=org_kms_key_id,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )


def _quarantine_new_claim_file(
    session: Session, facility_id: uuid.UUID, claim_file_id: uuid.UUID, *, actor: str, reason: str
) -> ClaimFileOutcome:
    repository.update_claim_file_status(
        session, facility_id, claim_file_id, status="quarantined", quarantine_reason=reason
    )
    repository.write_audit_log(
        session,
        facility_id,
        actor=actor,
        action="claim_file_quarantined",
        resource_type="claim_file",
        resource_id=str(claim_file_id),
        phi_accessed=False,
    )
    return ClaimFileOutcome(
        claim_file_id=claim_file_id,
        status="quarantined",
        claims_enriched=0,
        claims_unmatched=0,
        quarantine_reason=reason,
    )


def _ingest_837_impl(
    session: Session,
    facility_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
    encryptor: EnvelopeEncryptor,
) -> ClaimFileOutcome | DuplicateClaimFileOutcome:
    """837 is enrichment, not a parallel pricing/finding pipeline (see
    `domain.x837`'s own module docstring) -- an 837 claim only ever
    attaches diagnosis codes/rendering provider to a claim an 835 already
    created, correlated by `patient_control_number`. A claim that can't
    be matched is skipped, not a whole-file quarantine -- the reverse of
    `ingestion.plan`'s unmatched-reversal handling (F-01,
    docs/audit/REGISTER.md): a missing 835 counterpart is an expected,
    recoverable ordering situation (the 837 arrived first, or its 835
    never will), not evidence of a financial-integrity bug the way a
    reversal that can't net against anything is."""
    file_hash = hashlib.sha256(content).hexdigest()

    claim_file, is_new = repository.record_claim_file_if_new(
        session, facility_id, file_hash, source=source, uploaded_by=uploaded_by
    )
    if not is_new:
        return DuplicateClaimFileOutcome(claim_file_id=claim_file.id)

    scan_result = scanner.scan(content)
    if not scan_result.clean:
        return _quarantine_new_claim_file(
            session,
            facility_id,
            claim_file.id,
            actor=uploaded_by,
            reason=f"virus scan flagged this file: {scan_result.detail}",
        )

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _quarantine_new_claim_file(
            session,
            facility_id,
            claim_file.id,
            actor=uploaded_by,
            reason=f"could not decode file as UTF-8: {exc}",
        )

    parse_result = parse_837(text)
    total_claims = sum(len(txn.claims) for txn in parse_result.transactions)
    if total_claims == 0:
        reason = (
            f"no usable claims parsed: {parse_result.errors[0].reason}"
            if parse_result.errors
            else "no claims could be parsed from this file and no diagnostic was recorded"
        )
        return _quarantine_new_claim_file(
            session, facility_id, claim_file.id, actor=uploaded_by, reason=reason
        )

    # Same org_kms_key_id resolution ingestion.apply's 835 path uses --
    # diagnosis codes are PHI (health condition data), encrypted with the
    # same per-org key an org's other PHI columns use (Phase 6).
    org_id = repository.get_org_id_for_facility(session, facility_id)
    org_kms_key_id = (
        repository.get_organization_kms_key_id(session, org_id) if org_id is not None else None
    )

    claims_enriched = 0
    claims_unmatched = 0
    for txn in parse_result.transactions:
        for claim837 in txn.claims:
            matches = repository.get_claims_by_patient_control_number(
                session, facility_id, claim837.patient_control_number
            )
            if not matches:
                claims_unmatched += 1
                continue
            diagnosis_codes_encrypted = (
                encrypt_phi_field(
                    encryptor, json.dumps(list(claim837.diagnosis_codes)), kek_id=org_kms_key_id
                )
                if claim837.diagnosis_codes
                else None
            )
            rendering_provider_name = (
                claim837.rendering_provider.name
                if claim837.rendering_provider is not None
                else None
            )
            for claim_row in matches:
                repository.enrich_claim_from_837(
                    session,
                    claim_row.id,
                    diagnosis_codes_encrypted=diagnosis_codes_encrypted,
                    rendering_provider_name=rendering_provider_name,
                )
                claims_enriched += 1

    repository.update_claim_file_status(
        session,
        facility_id,
        claim_file.id,
        status="ingested",
        claims_enriched=claims_enriched,
        claims_unmatched=claims_unmatched,
    )
    repository.write_audit_log(
        session,
        facility_id,
        actor=uploaded_by,
        action="claim_file_ingested",
        resource_type="claim_file",
        resource_id=str(claim_file.id),
        phi_accessed=claims_enriched > 0,
    )
    return ClaimFileOutcome(
        claim_file_id=claim_file.id,
        status="ingested",
        claims_enriched=claims_enriched,
        claims_unmatched=claims_unmatched,
    )


def _to_prior_finding(row: FindingModel) -> PriorFinding:
    return PriorFinding(
        line_index=row.line_index,
        procedure_code=row.procedure_code,
        expected_allowed=None if row.expected_allowed is None else Money(row.expected_allowed),
        actual_allowed=Money(row.actual_allowed),
        shortfall=Money(row.shortfall),
        root_cause=row.root_cause,
        service_line_id=row.service_line_id,
    )
