"""BPR-to-claims reconciliation.

Pure: takes an already-parsed domain.x835.Transaction835, no I/O. The BPR
segment's reported total payment must equal the sum of what the claims in
the same transaction report as paid, plus any PLB adjustments -- a mismatch
usually means either a parser bug or a payer error, and both matter enough
to flag rather than silently trust the BPR figure.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.money import Money
from domain.x835 import Transaction835

RECONCILIATION_TOLERANCE = Money("0.01")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    matches: bool
    bpr_total: Money
    computed_total: Money
    difference: Money


def reconcile_bpr(transaction: Transaction835) -> ReconciliationResult:
    claims_total = sum(
        (claim.total_paid_reported for claim in transaction.claims), Money.zero()
    )
    plb_total = sum(
        (plb.amount for plb in transaction.plb_adjustments), Money.zero()
    )
    computed_total = claims_total + plb_total
    difference = transaction.bpr_total_paid - computed_total
    return ReconciliationResult(
        matches=abs(difference) <= RECONCILIATION_TOLERANCE,
        bpr_total=transaction.bpr_total_paid,
        computed_total=computed_total,
        difference=difference,
    )
