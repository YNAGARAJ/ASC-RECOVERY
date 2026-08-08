"""Pure ingestion planning: turns an already-parsed domain.x835.ParseResult
plus pre-fetched contract/finding data into a plan of what should be
persisted. No I/O anywhere in this module -- the DB fetches that feed it
(contract versions per payer, prior findings per reversed claim) and the DB
writes that consume its output both live in ingestion.pipeline/apply. This
split is what makes quarantine decisions, partial-batch handling, BPR
reconciliation, and reversal netting testable without a live Postgres, the
same way domain/ is testable without one.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from domain.contract import ClaimLineInput, ContractVersion, find_effective_contract, price_claim
from domain.money import Money
from domain.variance import ActualServiceLine, Finding, RootCause, evaluate_claim
from domain.x835 import Claim835, ClaimStatus, Entity, ParseIssue, ParseResult, Transaction835
from ingestion.reconcile import ReconciliationResult, reconcile_bpr


@dataclass(frozen=True, slots=True)
class PriorFinding:
    """A minimal, DB-independent carrier for a previously persisted finding
    -- what a reversal needs to net itself to zero against. `root_cause` is
    the RootCause enum's `.name` string, the same form it's stored as in
    the findings table, so callers don't need domain.variance.Finding's
    other DB-only fields (rule_version, service_line_id, ...) just to
    supply this."""

    line_index: int
    procedure_code: str
    expected_allowed: Money | None
    actual_allowed: Money
    shortfall: Money
    root_cause: str
    # The original finding's own, already-persisted service_line_id -- a
    # reversal must net against *this* line, not against whatever line sits
    # at the same line_index on the reversal claim, which may not exist or
    # may be a different procedure. See docs/audit/REGISTER.md F-01.
    service_line_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ClaimIngestionPlan:
    claim: Claim835
    date_of_service: date | None
    contract_version: ContractVersion | None
    findings: tuple[Finding, ...]
    is_reversal: bool
    skip_reason: str | None


@dataclass(frozen=True, slots=True)
class TransactionIngestionPlan:
    reconciliation: ReconciliationResult
    claims: tuple[ClaimIngestionPlan, ...]


@dataclass(frozen=True, slots=True)
class FileIngestionPlan:
    quarantine_reason: str | None
    transactions: tuple[TransactionIngestionPlan, ...]
    parse_errors: tuple[ParseIssue, ...]


def payer_key(payer: Entity | None) -> str:
    """835 payer identification (N1*PR) is meant to carry a real payer id
    in the id_code element (N104), but plenty of real-world feeds -- and
    every fixture in this repo -- omit it and rely on the payer name alone.
    Prefer the id when present, fall back to name so lookups still work."""
    if payer is None:
        return ""
    return payer.id_code if payer.id_code else payer.name


def _derive_date_of_service(claim: Claim835) -> date | None:
    line_dates = [sl.service_date for sl in claim.service_lines if sl.service_date is not None]
    if line_dates:
        return min(line_dates)
    for date_ref in claim.dates:
        if date_ref.qualifier == "232":
            return date_ref.value
    if claim.dates:
        return claim.dates[0].value
    return None


def _reverse_finding(claim: Claim835, prior: PriorFinding) -> Finding:
    return Finding(
        claim_id=claim.payer_claim_control_number,
        line_index=prior.line_index,
        procedure_code=prior.procedure_code,
        expected_allowed=prior.expected_allowed,
        actual_allowed=prior.actual_allowed,
        shortfall=-prior.shortfall,
        root_cause=RootCause[prior.root_cause],
        evidence=(
            f"Reversal of prior finding on {prior.procedure_code}: original "
            f"shortfall {prior.shortfall} netted to zero by reversal of claim "
            f"{claim.payer_claim_control_number}"
        ),
        # Never keyed off the reversal claim's own line layout -- see
        # PriorFinding.service_line_id's docstring and F-01.
        service_line_id=prior.service_line_id,
    )


def _plan_claim(
    claim: Claim835,
    payer_key: str,
    payer_versions: Sequence[ContractVersion],
    prior_findings_by_control_number: Mapping[str, Sequence[PriorFinding]],
) -> ClaimIngestionPlan:
    if claim.status is ClaimStatus.REVERSAL_OF_PREVIOUS_PAYMENT:
        priors = prior_findings_by_control_number.get(claim.payer_claim_control_number, ())
        findings = tuple(_reverse_finding(claim, prior) for prior in priors)
        return ClaimIngestionPlan(
            claim=claim,
            date_of_service=_derive_date_of_service(claim),
            contract_version=None,
            findings=findings,
            is_reversal=True,
            skip_reason=None,
        )

    date_of_service = _derive_date_of_service(claim)
    if date_of_service is None:
        return ClaimIngestionPlan(
            claim=claim,
            date_of_service=None,
            contract_version=None,
            findings=(),
            is_reversal=False,
            skip_reason="no service date on any line or claim-level DTM*232 to price against",
        )

    contract_version = find_effective_contract(payer_key, date_of_service, payer_versions)
    if contract_version is None:
        return ClaimIngestionPlan(
            claim=claim,
            date_of_service=date_of_service,
            contract_version=None,
            findings=(),
            is_reversal=False,
            skip_reason=(
                f"no contract version effective for payer {payer_key!r} on {date_of_service}"
            ),
        )

    # invoice_cost is always None here -- KNOWN, DELIBERATE, and still
    # open (F-17, docs/audit/REGISTER.md). An implant's invoice cost (what
    # the ASC itself paid its supplier for the device) is not carried on
    # the 835 remittance at all -- it can only come from a separate
    # purchasing-feed integration, and no phase of this build has ever
    # built one; there is no table, no upload mechanism, no API surface
    # for it anywhere in this codebase. domain.contract.price_claim's
    # implant carve-out is fully correct and unit-tested (see
    # tests/domain/test_contract.py) but cannot fire for real until that
    # integration exists -- until then, an implant line correctly surfaces
    # as an UNPRICED_CODE finding with shortfall=0 (see
    # tests/ingestion/test_plan.py, "detected via revenue code but stays
    # unpriced"), never a wrong dollar figure. `revenue_code` below IS real now
    # (B-16, fixed alongside F-17's triage this session) -- implant
    # *detection* works on the real path even though implant *pricing*
    # deliberately still doesn't.
    lines = tuple(
        ClaimLineInput(
            procedure_code=sl.procedure_code,
            modifiers=sl.modifiers,
            revenue_code=sl.revenue_code,
            charge=sl.charge,
            invoice_cost=None,
            units=sl.units,
        )
        for sl in claim.service_lines
    )
    priced = price_claim(lines, contract_version)
    actual_lines = tuple(
        ActualServiceLine(
            procedure_code=sl.procedure_code,
            service_date=sl.service_date,
            charge=sl.charge,
            allowed=sl.allowed,
        )
        for sl in claim.service_lines
    )
    findings = evaluate_claim(
        claim_id=claim.payer_claim_control_number,
        expected=priced,
        actual_lines=actual_lines,
        all_contract_versions=payer_versions,
    )
    return ClaimIngestionPlan(
        claim=claim,
        date_of_service=date_of_service,
        contract_version=contract_version,
        findings=findings,
        is_reversal=False,
        skip_reason=None,
    )


def _plan_transaction(
    transaction: Transaction835,
    contract_versions_by_payer: Mapping[str, Sequence[ContractVersion]],
    prior_findings_by_control_number: Mapping[str, Sequence[PriorFinding]],
) -> TransactionIngestionPlan:
    key = payer_key(transaction.payer)
    payer_versions = contract_versions_by_payer.get(key, ())
    claims = tuple(
        _plan_claim(claim, key, payer_versions, prior_findings_by_control_number)
        for claim in transaction.claims
    )
    return TransactionIngestionPlan(reconciliation=reconcile_bpr(transaction), claims=claims)


def _quarantine_reason(errors: Sequence[ParseIssue]) -> str:
    if not errors:
        return "no claims could be parsed from this file and no diagnostic was recorded"
    top = errors[0]
    reason = (
        f"no usable claims parsed: {top.segment_tag} at segment "
        f"{top.segment_index}: {top.reason}"
    )
    if len(errors) > 1:
        reason += f" (+{len(errors) - 1} more issue(s))"
    return reason


def _unmatched_reversal_control_numbers(
    transactions: Sequence[TransactionIngestionPlan],
) -> list[str]:
    """F-01 (docs/audit/REGISTER.md, the audit's one CRITICAL finding):
    a reversal claim's `findings` is empty if and only if no prior
    findings were found for its control number (`_plan_claim`'s reversal
    branch calls `_reverse_finding` once per prior, and a genuinely
    zero-variance original claim still has a `CORRECT_NO_VARIANCE`
    finding row per `domain.variance.evaluate_claim`'s own unconditional
    per-line `Finding` -- so an empty tuple here can only mean the
    original claim was never found, never that it was found and
    correct). Silently ingesting such a reversal is exactly the
    "silently doing nothing is worse than being wrong noisily" failure
    the amendment names -- the whole file quarantines instead."""
    return sorted(
        {
            claim_plan.claim.payer_claim_control_number
            for txn in transactions
            for claim_plan in txn.claims
            if claim_plan.is_reversal and not claim_plan.findings
        }
    )


def build_ingestion_plan(
    parse_result: ParseResult,
    *,
    contract_versions_by_payer: Mapping[str, Sequence[ContractVersion]],
    prior_findings_by_control_number: Mapping[str, Sequence[PriorFinding]],
) -> FileIngestionPlan:
    total_claims = sum(len(txn.claims) for txn in parse_result.transactions)
    if total_claims == 0:
        return FileIngestionPlan(
            quarantine_reason=_quarantine_reason(parse_result.errors),
            transactions=(),
            parse_errors=parse_result.errors,
        )

    transactions = tuple(
        _plan_transaction(txn, contract_versions_by_payer, prior_findings_by_control_number)
        for txn in parse_result.transactions
    )

    unmatched = _unmatched_reversal_control_numbers(transactions)
    if unmatched:
        return FileIngestionPlan(
            quarantine_reason=(
                "reversal claim(s) found no matching prior finding(s) to net against "
                f"-- payer claim control number(s): {', '.join(unmatched)}"
            ),
            transactions=(),
            parse_errors=parse_result.errors,
        )

    return FileIngestionPlan(
        quarantine_reason=None,
        transactions=transactions,
        parse_errors=parse_result.errors,
    )
