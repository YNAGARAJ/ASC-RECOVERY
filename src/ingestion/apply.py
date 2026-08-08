"""Persistence for a FileIngestionPlan.

Deliberately thin: every decision (quarantine, reversal netting,
reconciliation) already happened in ingestion.plan (pure, fully tested
without a live DB). This module's only job is turning that decision into
DB writes -- so it's only meaningfully testable against a real Postgres,
same as the rest of tests/db/, and skips the same way without one.

`org_kms_key_id` (Phase 6, per-org encryption keys,
`docs/MASTER-BUILD-PROMPT-V2.md`) is resolved once by the caller
(`ingestion/pipeline.py`, which already resolves the claim's org for
contract lookups) and threaded down to `encrypt_phi_field` for every
claim in the file -- `None` means the org has no dedicated key, so
patient PHI encrypts under the platform default
(`EnvelopeEncryptor.encrypt`'s own fallback).

`on_progress`/`should_cancel` (Phase 7, async job infrastructure,
`docs/MASTER-BUILD-PROMPT-V2.md`) are optional callbacks checked every
`_CALLBACK_INTERVAL` claims, not every single one -- `should_cancel` is
expected to be a DB read (`db.repository.is_cancel_requested` via
`src/jobs/runner.py`), so checking it on every claim would add a
round-trip per claim for no real responsiveness benefit. Both default to
`None`/no-op, so every existing caller (`scripts/ingestion/
poll_remittances.py`, every test in `tests/ingestion/`) is unaffected.
`should_cancel` returning `True` raises `JobCancelledError` -- this function
does no transaction management of its own (module docstring above), so
the exception propagates to whatever `access_session` block the caller
opened, which rolls back everything this job wrote so far; the worker
only records the terminal `cancelled` status afterward, on a separate
connection, once the rollback has already happened. A cancelled job
therefore leaves no partial claims/findings behind, same "clean slate"
property idempotent re-processing already relies on elsewhere in this
module.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from db import repository
from domain.contract import ContractVersion
from domain.variance import Finding
from ingestion.plan import ClaimIngestionPlan, FileIngestionPlan
from security.encryption import EnvelopeEncryptor
from security.phi_columns import encrypt_phi_field

ContractVersionIds = Mapping[tuple[str, date], uuid.UUID]

_CALLBACK_INTERVAL = 25


class JobCancelledError(Exception):
    """Raised by `apply_ingestion_plan` when `should_cancel()` returns
    `True` mid-file -- `src/jobs/runner.py` catches this to record the
    job as `cancelled` rather than `failed`."""


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    remittance_id: uuid.UUID
    status: str
    claims_created: int
    findings_created: int
    reconciliation_mismatches: int
    dollars_detected: Decimal


def _resolve_contract_version_id(
    contract_version: ContractVersion | None, contract_version_ids: ContractVersionIds
) -> uuid.UUID | None:
    if contract_version is None:
        return None
    return contract_version_ids.get((contract_version.payer_id, contract_version.effective_from))


def _apply_claim(
    session: Session,
    facility_id: uuid.UUID,
    remittance_id: uuid.UUID,
    claim_plan: ClaimIngestionPlan,
    contract_version_ids: ContractVersionIds,
    *,
    actor: str,
    encryptor: EnvelopeEncryptor,
    org_kms_key_id: str | None,
) -> Sequence[Finding]:
    claim = claim_plan.claim
    if claim_plan.date_of_service is None:
        # Callers only reach here when skip_reason is None, which plan.py
        # never sets without also setting date_of_service -- but this is a
        # NOT NULL DB column, and `assert` is stripped under `python -O`,
        # so a silent invariant isn't good enough here.
        raise ValueError(
            f"claim {claim.payer_claim_control_number!r} has no date_of_service "
            "but was not marked skip_reason -- refusing to write a NULL into "
            "a NOT NULL column"
        )

    claim_row = repository.create_claim(
        session,
        facility_id,
        remittance_id,
        patient_control_number=claim.patient_control_number,
        payer_claim_control_number=claim.payer_claim_control_number,
        status=claim.status.value,
        date_of_service=claim_plan.date_of_service,
        total_charge=claim.total_charge.as_decimal(),
        total_paid_reported=claim.total_paid_reported.as_decimal(),
        patient_responsibility=claim.patient_responsibility.as_decimal(),
        patient_name_encrypted=encrypt_phi_field(
            encryptor,
            claim.patient.name if claim.patient is not None else None,
            kek_id=org_kms_key_id,
        ),
        patient_member_id_encrypted=encrypt_phi_field(
            encryptor,
            claim.patient.id_code if claim.patient is not None else None,
            kek_id=org_kms_key_id,
        ),
        # Phase 9 (docs/MASTER-BUILD-PROMPT-V2.md): already parsed off
        # every 835 (domain.x835.Claim835.rendering_provider, from
        # NM1*82) but previously dropped before persistence -- not PHI,
        # so no encryption needed. An 837, if one arrives later, may
        # overwrite this via db.repository.enrich_claim_from_837.
        rendering_provider_name=(
            claim.rendering_provider.name if claim.rendering_provider is not None else None
        ),
    )
    repository.write_audit_log(
        session,
        facility_id,
        actor=actor,
        action="claim_ingested",
        resource_type="claim",
        resource_id=str(claim_row.id),
        phi_accessed=True,
    )

    service_line_ids: dict[int, uuid.UUID] = {}
    for line_index, service_line in enumerate(claim.service_lines):
        line_row = repository.create_service_line(
            session,
            facility_id,
            claim_row.id,
            line_index=line_index,
            procedure_code=service_line.procedure_code,
            modifiers=service_line.modifiers,
            revenue_code=service_line.revenue_code,
            charge=service_line.charge.as_decimal(),
            allowed=service_line.allowed.as_decimal(),
            paid_computed=service_line.paid_computed.as_decimal(),
            service_date=service_line.service_date,
            # Phase 9: already parsed off every 835 (SVC05) but
            # previously dropped before persistence -- see
            # domain.x835.ServiceLine835.units's own docstring.
            units=service_line.units,
        )
        service_line_ids[line_index] = line_row.id
        for adjustment in service_line.adjustments:
            repository.create_adjustment(
                session,
                facility_id,
                claim_row.id,
                line_row.id,
                group_code=adjustment.group_code.value,
                reason_code=adjustment.reason_code,
                amount=adjustment.amount.as_decimal(),
            )

    for adjustment in claim.adjustments:
        repository.create_adjustment(
            session,
            facility_id,
            claim_row.id,
            None,
            group_code=adjustment.group_code.value,
            reason_code=adjustment.reason_code,
            amount=adjustment.amount.as_decimal(),
        )

    # Every finding here is persistable: a regular finding's line_index
    # always corresponds to this same claim's own service lines (built from
    # the same loop above), and a reversal-netting finding carries its own
    # already-valid service_line_id instead of relying on line_index at all
    # (db.repository.save_findings uses it directly -- see F-01 in
    # docs/audit/REGISTER.md). Nothing here is silently dropped: a genuine
    # mismatch is now a real bug and should raise, not vanish.
    persistable = claim_plan.findings
    if persistable:
        finding_rows = repository.save_findings(
            session,
            facility_id,
            claim_row.id,
            service_line_ids,
            _resolve_contract_version_id(claim_plan.contract_version, contract_version_ids),
            persistable,
        )
        for finding_row in finding_rows:
            repository.write_audit_log(
                session,
                facility_id,
                actor=actor,
                action="finding_created",
                resource_type="finding",
                resource_id=str(finding_row.id),
                phi_accessed=False,
            )
    return persistable


def apply_ingestion_plan(
    session: Session,
    facility_id: uuid.UUID,
    plan: FileIngestionPlan,
    *,
    remittance_id: uuid.UUID,
    actor: str,
    contract_version_ids: ContractVersionIds,
    encryptor: EnvelopeEncryptor,
    org_kms_key_id: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> IngestionOutcome:
    if plan.quarantine_reason is not None:
        repository.update_remittance_status(
            session,
            facility_id,
            remittance_id,
            status="quarantined",
            quarantine_reason=plan.quarantine_reason,
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

    claims_created = 0
    findings_created = 0
    reconciliation_mismatches = 0
    dollars_detected = Decimal("0")
    total_claims = sum(
        1
        for transaction_plan in plan.transactions
        for claim_plan in transaction_plan.claims
        if claim_plan.skip_reason is None
    )

    for transaction_plan in plan.transactions:
        if not transaction_plan.reconciliation.matches:
            reconciliation_mismatches += 1
        for claim_plan in transaction_plan.claims:
            if claim_plan.skip_reason is not None:
                continue
            if (
                should_cancel is not None
                and claims_created % _CALLBACK_INTERVAL == 0
                and should_cancel()
            ):
                raise JobCancelledError(
                    f"cancellation requested after {claims_created}/{total_claims} claims"
                )
            persisted_findings = _apply_claim(
                session,
                facility_id,
                remittance_id,
                claim_plan,
                contract_version_ids,
                actor=actor,
                encryptor=encryptor,
                org_kms_key_id=org_kms_key_id,
            )
            findings_created += len(persisted_findings)
            dollars_detected += sum(
                (f.shortfall.as_decimal() for f in persisted_findings), Decimal("0")
            )
            claims_created += 1
            if on_progress is not None and claims_created % _CALLBACK_INTERVAL == 0:
                on_progress(claims_created, total_claims)

    if on_progress is not None:
        on_progress(claims_created, total_claims)

    repository.update_remittance_status(session, facility_id, remittance_id, status="ingested")
    repository.write_audit_log(
        session,
        facility_id,
        actor=actor,
        action="remittance_ingested",
        resource_type="remittance",
        resource_id=str(remittance_id),
        phi_accessed=True,
    )
    return IngestionOutcome(
        remittance_id=remittance_id,
        status="ingested",
        claims_created=claims_created,
        findings_created=findings_created,
        reconciliation_mismatches=reconciliation_mismatches,
        dollars_detected=dollars_detected,
    )
