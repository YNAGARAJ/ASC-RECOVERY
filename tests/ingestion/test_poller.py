"""Tests for F-18's poll->ingest orchestration (docs/audit/REGISTER.md).
Pure -- a fake IngestionSource and a fake, recording ingest_one callable,
no live Postgres needed. The real DB-touching path
(scripts/ingestion/poll_remittances.py's actual ingest_one closure) is
exactly ingestion.pipeline.ingest_file, already covered by
tests/ingestion/test_apply_*.py and friends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome
from ingestion.poller import poll_and_ingest
from ingestion.sources import IncomingFile


@dataclass
class _FakeSource:
    files: tuple[IncomingFile, ...]

    def poll(self) -> tuple[IncomingFile, ...]:
        return self.files


@dataclass
class _RecordingIngestOne:
    received: list[IncomingFile] = field(default_factory=list)

    def __call__(self, file: IncomingFile) -> IngestionOutcome | DuplicateOutcome:
        self.received.append(file)
        return IngestionOutcome(
            remittance_id=uuid.uuid4(),
            status="ingested",
            claims_created=1,
            findings_created=0,
            reconciliation_mismatches=0,
            dollars_detected=Decimal("0"),
        )


def test_no_files_polled_ingests_nothing() -> None:
    source = _FakeSource(files=())
    ingest_one = _RecordingIngestOne()

    result = poll_and_ingest(source, ingest_one)

    assert result == ()
    assert ingest_one.received == []


def test_each_polled_file_is_ingested_exactly_once() -> None:
    files = (
        IncomingFile(name="a.835", content=b"AAA", source="sftp"),
        IncomingFile(name="b.835", content=b"BBB", source="sftp"),
    )
    source = _FakeSource(files=files)
    ingest_one = _RecordingIngestOne()

    result = poll_and_ingest(source, ingest_one)

    assert ingest_one.received == list(files)
    assert [r.file_name for r in result] == ["a.835", "b.835"]
    assert all(r.outcome.status == "ingested" for r in result)  # type: ignore[union-attr]


def test_a_duplicate_outcome_is_passed_through_unchanged() -> None:
    files = (IncomingFile(name="a.835", content=b"AAA", source="s3"),)
    source = _FakeSource(files=files)
    duplicate_id = uuid.uuid4()

    def ingest_one(_file: IncomingFile) -> IngestionOutcome | DuplicateOutcome:
        return DuplicateOutcome(remittance_id=duplicate_id)

    result = poll_and_ingest(source, ingest_one)

    (only,) = result
    assert isinstance(only.outcome, DuplicateOutcome)
    assert only.outcome.remittance_id == duplicate_id
