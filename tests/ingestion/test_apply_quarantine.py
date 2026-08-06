"""A malformed file is quarantined with a persisted, readable diagnostic --
not silently dropped. Requires a live Postgres -- see
tests/ingestion/conftest.py."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.access import access_session
from db.models import Remittance as RemittanceModel
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import malformed_missing_isa
from tests.ingestion.conftest import make_test_encryptor, seed_org_with_contract


def test_malformed_file_is_quarantined_with_a_persisted_reason(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "Quarantine tenant")
    content = malformed_missing_isa().encode("utf-8")
    scanner = EicarAwareScanner()

    with access_session(app_session_factory, user_id) as session:
        outcome = ingest_file(
            session,
            facility_id,
            content=content,
            source="upload",
            uploaded_by="tester",
            scanner=scanner,
            encryptor=make_test_encryptor(),
        )

    assert isinstance(outcome, IngestionOutcome)
    assert outcome.status == "quarantined"
    assert outcome.claims_created == 0

    with access_session(app_session_factory, user_id) as session:
        row = session.get(RemittanceModel, outcome.remittance_id)
        assert row is not None
        assert row.status == "quarantined"
        assert row.quarantine_reason is not None
        assert "ISA" in row.quarantine_reason
