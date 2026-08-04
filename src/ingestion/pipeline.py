"""Top-level ingestion orchestrator -- the only place in ingestion/ doing DB
I/O directly. Wires: hash -> dedupe -> virus scan -> decode -> parse (pure,
domain.x835) -> fetch contract/prior-finding context -> plan (pure,
ingestion.plan) -> apply (ingestion.apply).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from db import repository
from db.models import Finding as FindingModel
from domain.contract import ContractVersion
from domain.money import Money
from domain.x835 import ClaimStatus, parse_835
from ingestion.apply import ContractVersionIds, IngestionOutcome, apply_ingestion_plan
from ingestion.plan import PriorFinding, build_ingestion_plan, payer_key
from ingestion.virus_scan import VirusScanner


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
    )


def ingest_file(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    content: bytes,
    source: str,
    uploaded_by: str,
    scanner: VirusScanner,
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
