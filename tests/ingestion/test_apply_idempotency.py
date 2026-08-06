"""Same file ingested 3x -> identical totals: exactly one remittance row,
one claim, one finding, and two DuplicateOutcome results. Requires a live
Postgres -- see tests/ingestion/conftest.py."""

from __future__ import annotations

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from db.access import access_session
from db.models import Claim as ClaimModel
from db.models import Finding as FindingModel
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor, seed_org_with_contract


def test_ingesting_the_same_file_three_times_creates_no_duplicates(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "Idempotent ingest tenant")
    content = minimal_valid_835().encode("utf-8")
    scanner = EicarAwareScanner()

    outcomes = []
    for _ in range(3):
        with access_session(app_session_factory, user_id) as session:
            outcomes.append(
                ingest_file(
                    session,
                    facility_id,
                    content=content,
                    source="upload",
                    uploaded_by="tester",
                    scanner=scanner,
                    encryptor=make_test_encryptor(),
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

    with access_session(app_session_factory, user_id) as session:
        claim_count = session.execute(
            select(func.count())
            .select_from(ClaimModel)
            .where(ClaimModel.facility_id == facility_id)
        ).scalar_one()
        finding_count = session.execute(
            select(func.count())
            .select_from(FindingModel)
            .where(FindingModel.facility_id == facility_id)
        ).scalar_one()

    assert claim_count == 1
    assert finding_count == 1
