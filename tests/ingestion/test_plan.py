"""Pure ingestion planning tests -- no I/O, no live Postgres needed, unlike
the DB-touching half of Phase 5's gate. Covers the four things the plan
layer is responsible for: quarantine decisions, partial-batch handling,
reversal netting, and determinism (same content -> same plan, the pure
proxy for "same file ingested 3x -> identical totals")."""

from __future__ import annotations

import uuid
from datetime import date

from domain.contract import ImplantCarveoutRule
from domain.money import Money
from domain.variance import RootCause
from domain.x835 import parse_835
from ingestion.plan import PriorFinding, build_ingestion_plan
from tests.domain.fixtures_x835 import (
    ELEMENT_SEP,
    SUB_ELEMENT_SEP,
    assemble,
    envelope_head,
    envelope_tail,
    malformed_clp,
    malformed_missing_isa,
    minimal_valid_835,
    partial_batch_one_bad_claim,
    reversal_835,
    seg,
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

    original_service_line_id = uuid.uuid4()
    prior = PriorFinding(
        line_index=original_finding.line_index,
        procedure_code=original_finding.procedure_code,
        expected_allowed=original_finding.expected_allowed,
        actual_allowed=original_finding.actual_allowed,
        shortfall=original_finding.shortfall,
        root_cause=original_finding.root_cause.name,
        service_line_id=original_service_line_id,
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
    # F-01 (docs/audit/REGISTER.md): the reversing finding must carry the
    # ORIGINAL's own service_line_id forward untouched -- persistence keys
    # off this directly rather than re-deriving it from the reversal
    # claim's own (possibly differently-shaped) service lines.
    assert reversal_finding.service_line_id == original_service_line_id


def test_reversal_service_line_id_is_independent_of_the_reversal_claims_own_lines() -> None:
    """F-01's core plan-layer guarantee, isolated: the reversing Finding's
    service_line_id comes only from the matched PriorFinding, never from
    the reversal claim's own line count or layout -- even a line_index that
    could never exist on the single-line `reversal_835()` fixture (index 7,
    on a claim with exactly one SVC line) must still net correctly and
    carry the original's service_line_id through untouched. This is what
    makes a reversal reporting fewer lines than the original safe at the
    plan layer; ingestion.apply/db.repository carrying it through correctly
    at persistence time is proven separately in
    tests/db/test_reversal_netting.py (DB-backed)."""
    original_service_line_id = uuid.uuid4()
    prior = PriorFinding(
        line_index=7,
        procedure_code="99213",
        expected_allowed=Money("50.00"),
        actual_allowed=Money("300.00"),
        shortfall=Money("250.00"),
        root_cause="UNDETERMINED_VARIANCE",
        service_line_id=original_service_line_id,
    )

    reversal_result = parse_835(reversal_835())
    reversal_plan = build_ingestion_plan(
        reversal_result,
        contract_versions_by_payer={},
        prior_findings_by_control_number={"PAYERCTRL0001": (prior,)},
    )
    (reversal_transaction,) = reversal_plan.transactions
    (reversal_claim,) = reversal_transaction.claims

    assert len(reversal_claim.findings) == 1
    reversal_finding = reversal_claim.findings[0]
    assert reversal_finding.line_index == 7
    assert reversal_finding.shortfall == Money("-250.00")
    assert reversal_finding.service_line_id == original_service_line_id


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


def test_implant_line_is_detected_via_revenue_code_but_stays_unpriced() -> None:
    """F-17 (docs/audit/REGISTER.md): documents the deliberate, remaining
    half of this finding. B-16's revenue-code fix (this same session)
    means an implant line is now correctly *detected* on the real
    ingestion path via SVC04 -- but pricing it still requires
    invoice_cost, which has no real data source anywhere in this
    codebase (no purchasing-feed integration exists; see
    ingestion/plan.py's own docstring). The line correctly surfaces as
    UNPRICED_CODE with zero shortfall -- never a wrong dollar figure,
    which is what CLAUDE.md actually requires -- and that's the intended
    behavior until a real invoice-cost source is built (tracked
    separately from Wave 3's bug-fix scope)."""
    implant_revenue_code = "0278"
    contract = make_contract_version(
        fee_schedule={},
        implant_carveout_rule=ImplantCarveoutRule(
            enabled=True,
            procedure_codes=frozenset(),
            revenue_codes=frozenset({implant_revenue_code}),
        ),
    )
    segments = [
        *envelope_head(),
        seg(ELEMENT_SEP, "LX", "1"),
        seg(
            ELEMENT_SEP,
            "CLP",
            "CLAIM0200",
            "1",
            "1750.00",
            "1750.00",
            "0.00",
            "12",
            "PAYERCTRL0200",
            "11",
        ),
        seg(ELEMENT_SEP, "DTM", "232", "20230110"),
        seg(
            ELEMENT_SEP,
            "SVC",
            f"HC{SUB_ELEMENT_SEP}L8699",
            "1750.00",
            "1750.00",
            implant_revenue_code,
            "1",
        ),
        seg(ELEMENT_SEP, "DTM", "472", "20230110"),
        *envelope_tail(segment_count="7"),
    ]
    result = parse_835(assemble(segments))

    plan = build_ingestion_plan(
        result,
        contract_versions_by_payer={TEST_PAYER: (contract,)},
        prior_findings_by_control_number={},
    )

    (transaction,) = plan.transactions
    (claim,) = transaction.claims
    assert claim.skip_reason is None
    (finding,) = claim.findings
    assert finding.root_cause == RootCause.UNPRICED_CODE
    assert finding.shortfall == Money.zero()
