"""Pure merge/sort logic for the auditor-facing claim access history --
the part worth unit-testing without a database. `db.repository.get_claim_access_history`
gathers rows from `audit_log` (writes: ingestion, finding evaluation,
packet generation/approval) and `phi_access_log` (reads: viewing a
finding's PHI-bearing detail, a packet's rendered text) and converts each
into an `AccessEvent` before calling `merge_access_history` here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AccessEvent:
    occurred_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str
    purpose: str | None


def merge_access_history(
    audit_entries: Sequence[AccessEvent], phi_entries: Sequence[AccessEvent]
) -> tuple[AccessEvent, ...]:
    """Chronological (oldest first) so a compliance officer reads the
    story of a claim top to bottom: ingested -> evaluated -> viewed ->
    packet drafted -> approved, in the order it actually happened."""
    return tuple(sorted((*audit_entries, *phi_entries), key=lambda event: event.occurred_at))
