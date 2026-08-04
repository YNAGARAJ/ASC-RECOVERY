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
from tests.ingestion.conftest import seed_tenant_with_contract


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
