"""DB-backed proof that F-18's poll_and_ingest actually ingests against a
real Postgres, using the real ingestion.pipeline.ingest_file as the
ingest_one closure -- exactly how scripts/ingestion/poll_remittances.py
wires it in production. Skips cleanly without TEST_DATABASE_URL, same
pattern as every other live-Postgres test in this repo. Written here,
never executed in this environment (no Docker/WSL/Postgres available).
"""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.access import access_session
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.poller import PollableOutcome, poll_and_ingest
from ingestion.sources import IncomingFile, IngestionSource
from ingestion.virus_scan import EicarAwareScanner
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor, seed_org_with_contract


class _OneShotSource(IngestionSource):
    def __init__(self, files: tuple[IncomingFile, ...]) -> None:
        self._files = files

    def poll(self) -> tuple[IncomingFile, ...]:
        return self._files


def test_poll_and_ingest_against_real_postgres(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = seed_org_with_contract(owner_engine, "poller-live")
    encryptor = make_test_encryptor()
    scanner = EicarAwareScanner()
    source = _OneShotSource(
        (IncomingFile(name="remit.835", content=minimal_valid_835().encode("utf-8"), source="s3"),)
    )

    def ingest_one(file: IncomingFile) -> PollableOutcome:
        with access_session(app_session_factory, user_id) as session:
            return ingest_file(
                session,
                facility_id,
                content=file.content,
                source=file.source,
                uploaded_by="s3-poller",
                scanner=scanner,
                encryptor=encryptor,
            )

    results = poll_and_ingest(source, ingest_one)

    assert len(results) == 1
    assert results[0].file_name == "remit.835"
    assert isinstance(results[0].outcome, IngestionOutcome)
    assert results[0].outcome.status == "ingested"


def test_polling_the_same_file_twice_is_a_duplicate_the_second_time(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    """Proves the module docstring's own claim: a fresh, empty `seen` set
    every script invocation (no persisted filename tracking) is
    harmless, not incorrect -- db.repository.record_remittance_if_new's
    content-hash check is what actually prevents double-ingestion."""
    user_id, facility_id = seed_org_with_contract(owner_engine, "poller-live-dup")
    encryptor = make_test_encryptor()
    scanner = EicarAwareScanner()
    content = minimal_valid_835().encode("utf-8")

    def ingest_one(file: IncomingFile) -> PollableOutcome:
        with access_session(app_session_factory, user_id) as session:
            return ingest_file(
                session,
                facility_id,
                content=file.content,
                source=file.source,
                uploaded_by="s3-poller",
                scanner=scanner,
                encryptor=encryptor,
            )

    first_source = _OneShotSource((IncomingFile(name="remit.835", content=content, source="s3"),))
    poll_and_ingest(first_source, ingest_one)

    # A second poll of an object store re-lists the same key (no
    # persisted "seen" state across script invocations) -- the second
    # ingest must come back a DuplicateOutcome, not a second claim set.
    second_source = _OneShotSource((IncomingFile(name="remit.835", content=content, source="s3"),))
    results = poll_and_ingest(second_source, ingest_one)

    assert isinstance(results[0].outcome, DuplicateOutcome)
