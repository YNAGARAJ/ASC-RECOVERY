"""Poll-source orchestration (F-18, docs/audit/REGISTER.md): the piece
that was missing between `ingestion.sources`'s fully-built
`SFTPPollSource`/`S3PollSource` and `ingestion.pipeline.ingest_file` --
both were tested in isolation but nothing in the running system ever
called `.poll()` and fed the result into ingestion. `poll_and_ingest`
closes that gap.

Deliberately pure: it calls `source.poll()` once and, for each returned
file, calls the caller-supplied `ingest_one` -- it does no I/O itself and
knows nothing about Postgres, `tenant_session`, or encryption. A real
caller (`scripts/ingestion/poll_remittances.py`) closes `ingest_one` over
a live DB session/tenant/encryptor; a test closes it over a fake,
recording callable. Same pure-orchestrator-over-an-injected-effect shape
`ingestion.plan`/`ingestion.apply` already split ingestion itself into,
so this loop is provably correct without needing a live Postgres.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ingestion.apply import IngestionOutcome
from ingestion.pipeline import ClaimFileOutcome, DuplicateClaimFileOutcome, DuplicateOutcome
from ingestion.sources import IncomingFile, IngestionSource

PollableOutcome = IngestionOutcome | DuplicateOutcome | ClaimFileOutcome | DuplicateClaimFileOutcome


@dataclass(frozen=True, slots=True)
class PolledOutcome:
    file_name: str
    outcome: PollableOutcome


def poll_and_ingest(
    source: IngestionSource,
    ingest_one: Callable[[IncomingFile], PollableOutcome],
) -> tuple[PolledOutcome, ...]:
    return tuple(PolledOutcome(file_name=f.name, outcome=ingest_one(f)) for f in source.poll())
