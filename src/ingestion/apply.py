"""Persistence for a FileIngestionPlan.

Deliberately thin: every decision (quarantine, reversal netting,
reconciliation) already happened in ingestion.plan (pure, fully tested
without a live DB). This module's only job is turning that decision into
DB writes -- so it's only meaningfully testable against a real Postgres,
same as the rest of tests/db/, and skips the same way without one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from db import repository
from domain.contract import ContractVersion
from ingestion.plan import ClaimIngestionPlan, FileIngestionPlan

ContractVersionIds = Mapping[tuple[str, date], uuid.UUID]


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    remittance_id: uuid.UUID
    status: str
    claims_created: int
    findings_created: int
    reconciliation_mismatches: int


def _resolve_contract_version_id(
    contract_version: ContractVersion | None, contract_version_ids: ContractVersionIds
) -> uuid.UUID | None:
    if contract_version is None:
        return None
    return contract_version_ids.get((contract_version.payer_id, contract_version.effective_from))


def _apply_claim(
    session: Session,
    tenant_id: uuid.UUID,
    remittance_id: uuid.UUID,
    claim_plan: ClaimIngestionPlan,
    contract_version_ids: ContractVersionIds,
) -> int:
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
        tenant_id,
        remittance_id,
        patient_control_number=claim.patient_control_number,
        payer_claim_control_number=claim.payer_claim_control_number,
        status=claim.status.value,
        date_of_service=claim_plan.date_of_service,
        total_charge=claim.total_charge.as_decimal(),
        total_paid_reported=claim.total_paid_reported.as_decimal(),
        patient_responsibility=claim.patient_responsibility.as_decimal(),
    )

    service_line_ids: dict[int, uuid.UUID] = {}
    for line_index, service_line in enumerate(claim.service_lines):
        line_row = repository.create_service_line(
            session,
            tenant_id,
            claim_row.id,
            line_index=line_index,
            procedure_code=service_line.procedure_code,
            modifiers=service_line.modifiers,
            revenue_code=service_line.revenue_code,
            charge=service_line.charge.as_decimal(),
            allowed=service_line.allowed.as_decimal(),
            paid_computed=service_line.paid_computed.as_decimal(),
            service_date=service_line.service_date,
        )
        service_line_ids[line_index] = line_row.id
        for adjustment in service_line.adjustments:
            repository.create_adjustment(
                session,
                tenant_id,
                claim_row.id,
                line_row.id,
                group_code=adjustment.group_code.value,
                reason_code=adjustment.reason_code,
                amount=adjustment.amount.as_decimal(),
            )

    for adjustment in claim.adjustments:
        repository.create_adjustment(
            session,
            tenant_id,
            claim_row.id,
            None,
            group_code=adjustment.group_code.value,
            reason_code=adjustment.reason_code,
            amount=adjustment.amount.as_decimal(),
        )

    # Findings referencing a line this claim doesn't have (e.g. a reversal
    # reporting fewer lines than what it's reversing) are dropped rather
    # than raising -- one malformed correlation must not fail the batch.
    persistable = tuple(f for f in claim_plan.findings if f.line_index in service_line_ids)
    if persistable:
        repository.save_findings(
            session,
            tenant_id,
            claim_row.id,
            service_line_ids,
            _resolve_contract_version_id(claim_plan.contract_version, contract_version_ids),
            persistable,
        )
    return len(persistable)


def apply_ingestion_plan(
    session: Session,
    tenant_id: uuid.UUID,
    plan: FileIngestionPlan,
    *,
    remittance_id: uuid.UUID,
    actor: str,
    contract_version_ids: ContractVersionIds,
) -> IngestionOutcome:
    if plan.quarantine_reason is not None:
        repository.update_remittance_status(
            session,
            tenant_id,
            remittance_id,
            status="quarantined",
            quarantine_reason=plan.quarantine_reason,
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
        )

    claims_created = 0
    findings_created = 0
    reconciliation_mismatches = 0

    for transaction_plan in plan.transactions:
        if not transaction_plan.reconciliation.matches:
            reconciliation_mismatches += 1
        for claim_plan in transaction_plan.claims:
            if claim_plan.skip_reason is not None:
                continue
            findings_created += _apply_claim(
                session, tenant_id, remittance_id, claim_plan, contract_version_ids
            )
            claims_created += 1

    repository.update_remittance_status(session, tenant_id, remittance_id, status="ingested")
    repository.write_audit_log(
        session,
        tenant_id,
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
    )
