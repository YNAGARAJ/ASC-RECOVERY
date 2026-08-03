"""Structural sanity tests for the Phase 2 synthetic 835 generator.

These tests validate evals/generator.py's output shape and its internal
consistency with the real domain pipeline (parse_835 -> price_claim ->
evaluate_claim). They do not touch the frozen evals/golden/cases.py file --
that file is a snapshot of build_golden_cases(), and these tests exercise
the generator function directly so a future change to generator.py is
caught here before anyone re-runs `python -m evals.generator` to refresh
the golden set.
"""

from __future__ import annotations

import re

import pytest
from evals.generator import (
    _TARGET_PER_CATEGORY,
    DefectType,
    GoldenCase,
    build_golden_cases,
)

from domain.contract import find_effective_contract, price_claim
from domain.variance import ActualServiceLine, evaluate_claim
from domain.x835 import parse_835

_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_MBI_PATTERN = re.compile(
    r"\b[0-9][A-Za-z][A-Za-z0-9][0-9]-[A-Za-z][A-Za-z0-9][0-9]-[A-Za-z0-9]{2}[A-Za-z][0-9]\b"
)


@pytest.fixture(scope="module")
def golden_cases() -> tuple[GoldenCase, ...]:
    return build_golden_cases()


def test_generates_expected_total_and_per_category_counts(
    golden_cases: tuple[GoldenCase, ...],
) -> None:
    assert len(golden_cases) == _TARGET_PER_CATEGORY * len(DefectType)
    counts: dict[DefectType, int] = {defect: 0 for defect in DefectType}
    for case in golden_cases:
        counts[case.defect_type] += 1
    for defect in DefectType:
        assert counts[defect] == _TARGET_PER_CATEGORY, defect


def test_build_golden_cases_is_deterministic() -> None:
    first = build_golden_cases()
    second = build_golden_cases()
    assert first == second


def test_case_ids_are_unique(golden_cases: tuple[GoldenCase, ...]) -> None:
    ids = [case.case_id for case in golden_cases]
    assert len(ids) == len(set(ids))


def test_patient_control_numbers_are_unique(golden_cases: tuple[GoldenCase, ...]) -> None:
    pcns = [case.patient_control_number for case in golden_cases]
    assert len(pcns) == len(set(pcns))


def test_every_case_parses_with_no_errors(golden_cases: tuple[GoldenCase, ...]) -> None:
    for case in golden_cases:
        result = parse_835(case.x835_text)
        assert result.errors == (), case.case_id
        assert len(result.transactions) == 1, case.case_id


def test_every_case_contains_its_claim(golden_cases: tuple[GoldenCase, ...]) -> None:
    for case in golden_cases:
        result = parse_835(case.x835_text)
        claim_ids = [c.patient_control_number for c in result.transactions[0].claims]
        assert case.patient_control_number in claim_ids, case.case_id


def test_no_identifier_matches_phi_shaped_patterns(golden_cases: tuple[GoldenCase, ...]) -> None:
    for case in golden_cases:
        assert not _SSN_PATTERN.search(case.x835_text), case.case_id
        assert not _MBI_PATTERN.search(case.x835_text), case.case_id
        assert not case.patient_control_number[0].isdigit(), case.case_id


def test_expected_findings_align_with_claim_line_count(
    golden_cases: tuple[GoldenCase, ...],
) -> None:
    for case in golden_cases:
        if case.excluded_from_scoring:
            assert case.expected_findings == ()
            continue
        assert len(case.expected_findings) == len(case.claim_lines), case.case_id
        for idx, finding in enumerate(case.expected_findings):
            assert finding.line_index == idx, case.case_id


def test_reversal_cases_are_excluded_and_carry_reversal_status(
    golden_cases: tuple[GoldenCase, ...],
) -> None:
    from domain.x835 import ClaimStatus

    reversal_cases = [c for c in golden_cases if c.defect_type is DefectType.REVERSAL_AFTER_PAYMENT]
    assert len(reversal_cases) == _TARGET_PER_CATEGORY
    for case in reversal_cases:
        assert case.excluded_from_scoring
        result = parse_835(case.x835_text)
        claim = result.transactions[0].claims[0]
        assert claim.status is ClaimStatus.REVERSAL_OF_PREVIOUS_PAYMENT


def test_control_cases_are_all_correct_no_variance(golden_cases: tuple[GoldenCase, ...]) -> None:
    from domain.money import Money
    from domain.variance import RootCause

    control_cases = [c for c in golden_cases if c.defect_type is DefectType.CORRECT_PAYMENT]
    assert len(control_cases) == _TARGET_PER_CATEGORY
    for case in control_cases:
        for finding in case.expected_findings:
            assert finding.root_cause is RootCause.CORRECT_NO_VARIANCE, case.case_id
            assert finding.shortfall == Money.zero(), case.case_id


def test_full_pipeline_matches_ground_truth_for_every_scored_case(
    golden_cases: tuple[GoldenCase, ...],
) -> None:
    """The decisive check: run the real parse -> price -> evaluate pipeline
    over every generated case and assert it reproduces this case's
    independently-derived ground truth exactly. This is what makes the
    golden dataset trustworthy before it's ever frozen to disk."""
    for case in golden_cases:
        if case.excluded_from_scoring:
            continue
        result = parse_835(case.x835_text)
        claim = next(
            c
            for c in result.transactions[0].claims
            if c.patient_control_number == case.patient_control_number
        )
        actual_lines = tuple(
            ActualServiceLine(sl.procedure_code, sl.service_date, sl.charge, sl.allowed)
            for sl in claim.service_lines
        )
        contract = find_effective_contract(
            case.payer_id, case.date_of_service, case.contract_versions
        )
        assert contract is not None, case.case_id
        expected = price_claim(case.claim_lines, contract)
        findings = evaluate_claim(
            case.case_id, expected, actual_lines, all_contract_versions=case.contract_versions
        )
        assert len(findings) == len(case.expected_findings), case.case_id
        for finding, ground_truth in zip(findings, case.expected_findings, strict=True):
            assert finding.root_cause == ground_truth.root_cause, (case.case_id, finding)
            assert finding.shortfall == ground_truth.shortfall, (case.case_id, finding)
            assert finding.expected_allowed == ground_truth.expected_allowed, (
                case.case_id,
                finding,
            )
