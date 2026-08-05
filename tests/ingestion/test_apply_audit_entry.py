"""One audit_log row per ingested file, with the outcome and actor
recorded -- CLAUDE.md rule 5, every write to a PHI-bearing table goes
through the audit log. Requires a live Postgres -- see
tests/ingestion/conftest.py."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from db.models import AuditLog as AuditLogModel
from db.tenancy import tenant_session
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor, seed_tenant_with_contract


def test_ingesting_a_file_writes_exactly_one_audit_entry(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = seed_tenant_with_contract(app_session_factory, "Audit entry tenant")
    content = minimal_valid_835().encode("utf-8")
    scanner = EicarAwareScanner()

    with tenant_session(app_session_factory, tenant_id) as session:
        outcome = ingest_file(
            session,
            tenant_id,
            content=content,
            source="upload",
            uploaded_by="audit-tester",
            scanner=scanner,
            encryptor=make_test_encryptor(),
        )

    assert isinstance(outcome, IngestionOutcome)

    with tenant_session(app_session_factory, tenant_id) as session:
        entries = (
            session.execute(
                select(AuditLogModel).where(
                    AuditLogModel.tenant_id == tenant_id,
                    AuditLogModel.resource_id == str(outcome.remittance_id),
                )
            )
            .scalars()
            .all()
        )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "remittance_ingested"
    assert entry.actor == "audit-tester"
    assert entry.resource_type == "remittance"
    assert entry.phi_accessed is True


def test_ingesting_a_file_also_writes_claim_and_finding_audit_entries(
    app_session_factory: sessionmaker[Session],
) -> None:
    """`GET /claims/{id}/access-history` (Phase 8) reconstructs a claim's
    history from `audit_log` rows with resource_type "claim"/"finding" --
    without these, ingestion itself is invisible to that report, even
    though it's the first (and most PHI-bearing) thing that happens to a
    claim."""
    tenant_id = seed_tenant_with_contract(app_session_factory, "Claim audit tenant")
    content = minimal_valid_835().encode("utf-8")
    scanner = EicarAwareScanner()

    with tenant_session(app_session_factory, tenant_id) as session:
        outcome = ingest_file(
            session,
            tenant_id,
            content=content,
            source="upload",
            uploaded_by="audit-tester",
            scanner=scanner,
            encryptor=make_test_encryptor(),
        )
    assert isinstance(outcome, IngestionOutcome)
    assert outcome.claims_created == 1
    assert outcome.findings_created == 1

    with tenant_session(app_session_factory, tenant_id) as session:
        claim_entries = (
            session.execute(
                select(AuditLogModel).where(
                    AuditLogModel.tenant_id == tenant_id,
                    AuditLogModel.resource_type == "claim",
                )
            )
            .scalars()
            .all()
        )
        finding_entries = (
            session.execute(
                select(AuditLogModel).where(
                    AuditLogModel.tenant_id == tenant_id,
                    AuditLogModel.resource_type == "finding",
                )
            )
            .scalars()
            .all()
        )

    assert len(claim_entries) == 1
    assert claim_entries[0].action == "claim_ingested"
    assert claim_entries[0].actor == "audit-tester"
    assert claim_entries[0].phi_accessed is True

    assert len(finding_entries) == 1
    assert finding_entries[0].action == "finding_created"
    assert finding_entries[0].actor == "audit-tester"
