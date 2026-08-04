"""BPR reconciliation: BPR total paid must equal sum(claim totals) +
sum(PLB amounts) within tolerance -- a mismatch usually means a parser bug
or a payer error."""

from __future__ import annotations

from domain.money import Money
from domain.x835 import parse_835
from ingestion.reconcile import reconcile_bpr
from tests.ingestion.fixtures import reconciling_835


def test_reconciling_bpr_matches() -> None:
    # claim total_paid 430.00 + PLB -10.00 == 420.00
    result = parse_835(reconciling_835(bpr_total="420.00"))
    (transaction,) = result.transactions

    reconciliation = reconcile_bpr(transaction)

    assert reconciliation.matches is True
    assert reconciliation.bpr_total == Money("420.00")
    assert reconciliation.computed_total == Money("420.00")
    assert reconciliation.difference == Money.zero()


def test_mismatched_bpr_is_flagged_not_silently_trusted() -> None:
    result = parse_835(reconciling_835(bpr_total="500.00"))
    (transaction,) = result.transactions

    reconciliation = reconcile_bpr(transaction)

    assert reconciliation.matches is False
    assert reconciliation.bpr_total == Money("500.00")
    assert reconciliation.computed_total == Money("420.00")
    assert reconciliation.difference == Money("80.00")


def test_reconciliation_within_one_cent_tolerance_still_matches() -> None:
    # 430.00 + (-10.00) = 420.00; one cent off should still be treated as reconciled
    result = parse_835(reconciling_835(bpr_total="420.01"))
    (transaction,) = result.transactions

    reconciliation = reconcile_bpr(transaction)

    assert reconciliation.matches is True
