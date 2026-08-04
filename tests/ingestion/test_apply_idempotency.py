"""Same file ingested 3x -> identical totals: exactly one remittance row,
one claim, one finding, and two DuplicateOutcome results. Requires a live
Postgres -- see tests/ingestion/conftest.py."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.models import Claim as ClaimModel
from db.models import Finding as FindingModel
from db.tenancy import tenant_session
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import seed_tenant_with_contract


def test_ingesting_the_same_file_three_times_creates_no_duplicates(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = seed_tenant_with_contract(app_session_factory, "Idempotent ingest tenant")
    content = minimal_valid_835().encode("utf-8")
    scanner = EicarAwareScanner()

    outcomes = []
    for _ in range(3):
        with tenant_session(app_session_factory, tenant_id) as session:
            outcomes.append(
                ingest_file(
                    session,
                    tenant_id,
                    content=content,
                    source="upload",
                    uploaded_by="tester",
                    scanner=scanner,
                )
            )

    first, second, third = outcomes
    assert isinstance(first, IngestionOutcome)
    assert first.status == "ingested"
    assert first.claims_created == 1
    assert first.findings_created == 1
    assert isinstance(second, DuplicateOutcome)
    assert isinstance(third, DuplicateOutcome)
    assert second.remittance_id == first.remittance_id
    assert third.remittance_id == first.remittance_id

    with tenant_session(app_session_factory, tenant_id) as session:
        claim_count = session.execute(
            select(func.count()).select_from(ClaimModel).where(ClaimModel.tenant_id == tenant_id)
        ).scalar_one()
        finding_count = session.execute(
            select(func.count())
            .select_from(FindingModel)
            .where(FindingModel.tenant_id == tenant_id)
        ).scalar_one()

    assert claim_count == 1
    assert finding_count == 1
