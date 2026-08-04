"""Pure ingestion planning tests -- no I/O, no live Postgres needed, unlike
the DB-touching half of Phase 5's gate. Covers the four things the plan
layer is responsible for: quarantine decisions, partial-batch handling,
reversal netting, and determinism (same content -> same plan, the pure
proxy for "same file ingested 3x -> identical totals")."""

from __future__ import annotations

from datetime import date

from domain.money import Money
from domain.x835 import parse_835
from ingestion.plan import PriorFinding, build_ingestion_plan
from tests.domain.fixtures_x835 import (
    malformed_clp,
    malformed_missing_isa,
    minimal_valid_835,
    partial_batch_one_bad_claim,
    reversal_835,
)
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version


def test_unparseable_file_is_quarantined_with_a_readable_reason() -> None:
    result = parse_835(malformed_missing_isa())

    plan = build_ingestion_plan(
        result, contract_versions_by_payer={}, prior_findings_by_control_number={}
    )

    assert plan.quarantine_reason is not None
    assert "ISA" in plan.quarantine_reason
    assert plan.transactions == ()


def test_file_with_only_malformed_claims_is_quarantined() -> None:
    result = parse_835(malformed_clp())

    plan = build_ingestion_plan(
        result, contract_versions_by_payer={}, prior_findings_by_control_number={}
    )

    assert plan.quarantine_reason is not None
    assert "CLP" in plan.quarantine_reason


def test_one_bad_claim_does_not_quarantine_the_whole_file() -> None:
    result = parse_835(partial_batch_one_bad_claim())
    contract = make_contract_version(
        fee_schedule={"99213": Money("100.00"), "99214": Money("150.00")}
    )

    plan = build_ingestion_plan(
        result,
        contract_versions_by_payer={TEST_PAYER: (contract,)},
        prior_findings_by_control_number={},
    )

    assert plan.quarantine_reason is None
    (transaction,) = plan.transactions
    # parser already dropped the malformed middle claim; the two good ones
    # (CLAIM0001 / PAYERCTRL0001 and CLAIM0006 / PAYERCTRL0006) must still
    # produce a plan entry each, with no error raised in between.
    assert len(transaction.claims) == 2
    assert all(c.skip_reason is None for c in transaction.claims)
    assert all(c.contract_version is not None for c in transaction.claims)


def test_reversal_nets_prior_finding_to_exactly_zero() -> None:
    original_result = parse_835(minimal_valid_835())
    contract = make_contract_version(fee_schedule={"99213": Money("100.00")})

    original_plan = build_ingestion_plan(
        original_result,
        contract_versions_by_payer={TEST_PAYER: (contract,)},
        prior_findings_by_control_number={},
    )
    (original_transaction,) = original_plan.transactions
    (original_claim,) = original_transaction.claims
    assert len(original_claim.findings) == 1
    original_finding = original_claim.findings[0]

    prior = PriorFinding(
        line_index=original_finding.line_index,
        procedure_code=original_finding.procedure_code,
        expected_allowed=original_finding.expected_allowed,
        actual_allowed=original_finding.actual_allowed,
        shortfall=original_finding.shortfall,
        root_cause=original_finding.root_cause.name,
    )

    reversal_result = parse_835(reversal_835())
    reversal_plan = build_ingestion_plan(
        reversal_result,
        contract_versions_by_payer={TEST_PAYER: (contract,)},
        prior_findings_by_control_number={"PAYERCTRL0001": (prior,)},
    )
    (reversal_transaction,) = reversal_plan.transactions
    (reversal_claim,) = reversal_transaction.claims

    assert reversal_claim.is_reversal is True
    assert len(reversal_claim.findings) == 1
    reversal_finding = reversal_claim.findings[0]

    assert reversal_finding.shortfall == -original_finding.shortfall
    assert original_finding.shortfall + reversal_finding.shortfall == Money.zero()


def test_same_content_produces_an_identical_plan() -> None:
    """Pure proxy for 'same file ingested 3x -> identical totals': the plan
    layer has no hidden state, so parsing and planning the same bytes twice
    must produce equal plans."""
    text = minimal_valid_835()
    contract = make_contract_version(fee_schedule={"99213": Money("100.00")})
    contract_versions_by_payer = {TEST_PAYER: (contract,)}

    plan_a = build_ingestion_plan(
        parse_835(text),
        contract_versions_by_payer=contract_versions_by_payer,
        prior_findings_by_control_number={},
    )
    plan_b = build_ingestion_plan(
        parse_835(text),
        contract_versions_by_payer=contract_versions_by_payer,
        prior_findings_by_control_number={},
    )

    assert plan_a == plan_b


def test_no_contract_effective_skips_claim_without_failing_the_batch() -> None:
    result = parse_835(minimal_valid_835())
    contract = make_contract_version(effective_from=date(2099, 1, 1))  # not yet effective

    plan = build_ingestion_plan(
        result,
        contract_versions_by_payer={TEST_PAYER: (contract,)},
        prior_findings_by_control_number={},
    )

    (transaction,) = plan.transactions
    (claim,) = transaction.claims
    assert claim.skip_reason is not None
    assert claim.findings == ()
