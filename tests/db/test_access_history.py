"""Pure merge/sort logic for the auditor report -- runs without a
database, unlike the rest of tests/db/."""

from __future__ import annotations

from datetime import UTC, datetime

from db.access_history import AccessEvent, merge_access_history


def _event(
    hour: int, action: str, resource_type: str = "claim", purpose: str | None = None
) -> AccessEvent:
    return AccessEvent(
        occurred_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
        actor=f"actor-{hour}",
        action=action,
        resource_type=resource_type,
        resource_id="11111111-1111-1111-1111-111111111111",
        purpose=purpose,
    )


def test_merges_and_sorts_chronologically_across_both_sources() -> None:
    audit_entries = [_event(10, "remittance_ingested"), _event(14, "packet_approved")]
    phi_entries = [_event(12, "finding_detail_viewed", purpose="claim_review")]

    merged = merge_access_history(audit_entries, phi_entries)

    assert [e.action for e in merged] == [
        "remittance_ingested",
        "finding_detail_viewed",
        "packet_approved",
    ]


def test_empty_inputs_produce_empty_history() -> None:
    assert merge_access_history([], []) == ()


def test_one_empty_source_still_returns_the_other_sorted() -> None:
    audit_entries = [_event(9, "b"), _event(8, "a")]
    merged = merge_access_history(audit_entries, [])
    assert [e.action for e in merged] == ["a", "b"]


def test_phi_entries_carry_a_purpose_audit_entries_may_not() -> None:
    phi_entries = [_event(10, "finding_detail_viewed", purpose="appeal_drafting")]
    merged = merge_access_history([], phi_entries)
    assert merged[0].purpose == "appeal_drafting"
