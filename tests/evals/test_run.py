"""Tests for the Phase 2 eval harness's scoring engine (evals/run.py).

Most tests here use hand-built mini GoldenCases with a stubbed evaluator
(the `evaluator` parameter score_cases() takes for exactly this purpose) so
the confusion-matrix/metrics arithmetic is verified independently of both
the generator and the real domain pipeline.

test_breaking_a_variance_rule_fails_the_recall_gate is the literal proof
required by the Phase 2 gate: "deliberately breaking one rule in
variance.py makes the eval fail." It drives the same evaluator seam
score_cases() takes in production, wrapping the real pipeline to simulate
the implant-carve-out rule silently failing, then asserts the eval's
recall drops below the gate and the run fails.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from evals.generator import DefectType, ExpectedFinding, GoldenCase
from evals.golden.cases import GOLDEN_CASES
from evals.run import ScoreResult, evaluate_case, main, score_cases

from domain.money import Money
from domain.variance import Finding, RootCause


def _mini_case(
    case_id: str,
    expected_findings: tuple[ExpectedFinding, ...],
    *,
    excluded: bool = False,
) -> GoldenCase:
    return GoldenCase(
        case_id=case_id,
        defect_type=DefectType.CORRECT_PAYMENT,
        x835_text="",
        patient_control_number="X",
        payer_id="P",
        date_of_service=date(2023, 1, 1),
        claim_lines=(),
        contract_versions=(),
        expected_findings=expected_findings,
        excluded_from_scoring=excluded,
    )


def _finding(
    line_index: int, root_cause: RootCause, shortfall: Money, expected_allowed: Money | None = None
) -> Finding:
    return Finding(
        claim_id="X",
        line_index=line_index,
        procedure_code="P",
        expected_allowed=expected_allowed,
        actual_allowed=Money.zero(),
        shortfall=shortfall,
        root_cause=root_cause,
        evidence="",
    )


# --- ScoreResult properties: zero-denominator edge cases ---------------------


def test_default_score_result_avoids_division_by_zero() -> None:
    result = ScoreResult()
    assert result.recall == Decimal("1")
    assert result.precision == Decimal("1")
    assert result.root_cause_accuracy == Decimal("1")
    assert result.dollar_accuracy == Decimal("1")
    assert result.total_lines == 0
    assert result.passed


# --- score_cases confusion-matrix arithmetic ----------------------------------


def test_true_positive_when_both_sides_agree_on_a_shortfall() -> None:
    case = _mini_case(
        "c1",
        (
            ExpectedFinding(
                0, "P", Money("100.00"), RootCause.BILATERAL_MODIFIER_DROPPED, Money("50.00")
            ),
        ),
    )
    result = score_cases(
        [case],
        evaluator=lambda c: (_finding(0, RootCause.BILATERAL_MODIFIER_DROPPED, Money("50.00")),),
    )
    assert result.true_positives == 1
    assert result.false_negatives == 0
    assert result.recall == Decimal("1")
    assert result.precision == Decimal("1")


def test_false_negative_when_real_underpayment_is_missed() -> None:
    case = _mini_case(
        "c1",
        (
            ExpectedFinding(
                0, "P", Money("100.00"), RootCause.BILATERAL_MODIFIER_DROPPED, Money("50.00")
            ),
        ),
    )
    result = score_cases(
        [case], evaluator=lambda c: (_finding(0, RootCause.CORRECT_NO_VARIANCE, Money.zero()),)
    )
    assert result.false_negatives == 1
    assert result.true_positives == 0
    assert result.recall == Decimal("0")
    assert not result.passed


def test_false_negative_when_shortfall_computed_but_mislabeled_correct() -> None:
    """A rule that still computes the right dollars but mislabels the line
    CORRECT_NO_VARIANCE must not be scored as a catch -- this is exactly the
    bug shape that motivated checking root_cause alongside shortfall in
    score_cases()."""
    case = _mini_case(
        "c1",
        (
            ExpectedFinding(
                0, "P", Money("100.00"), RootCause.IMPLANT_NOT_CARVED_OUT, Money("50.00")
            ),
        ),
    )
    result = score_cases(
        [case], evaluator=lambda c: (_finding(0, RootCause.CORRECT_NO_VARIANCE, Money("50.00")),)
    )
    assert result.false_negatives == 1
    assert result.true_positives == 0
    assert not result.passed


def test_false_positive_when_correct_payment_is_flagged() -> None:
    case = _mini_case(
        "c1",
        (ExpectedFinding(0, "P", Money("100.00"), RootCause.CORRECT_NO_VARIANCE, Money.zero()),),
    )
    result = score_cases(
        [case], evaluator=lambda c: (_finding(0, RootCause.UNDETERMINED_VARIANCE, Money("10.00")),)
    )
    assert result.false_positives == 1
    assert result.true_negatives == 0
    assert result.precision == Decimal("0")
    assert not result.passed


def test_true_negative_when_both_sides_agree_no_variance() -> None:
    case = _mini_case(
        "c1",
        (ExpectedFinding(0, "P", Money("100.00"), RootCause.CORRECT_NO_VARIANCE, Money.zero()),),
    )
    result = score_cases(
        [case], evaluator=lambda c: (_finding(0, RootCause.CORRECT_NO_VARIANCE, Money.zero()),)
    )
    assert result.true_negatives == 1
    assert result.total_lines == 1


def test_root_cause_accuracy_only_considers_lines_both_sides_flag_positive() -> None:
    case = _mini_case(
        "c1",
        (
            ExpectedFinding(
                0, "P", Money("100.00"), RootCause.BILATERAL_MODIFIER_DROPPED, Money("50.00")
            ),
            ExpectedFinding(
                1, "P", Money("200.00"), RootCause.IMPLANT_NOT_CARVED_OUT, Money("30.00")
            ),
        ),
    )

    def stub(c: GoldenCase) -> tuple[Finding, ...]:
        return (
            _finding(0, RootCause.BILATERAL_MODIFIER_DROPPED, Money("50.00")),  # matches
            _finding(1, RootCause.UNDETERMINED_VARIANCE, Money("30.00")),  # wrong root cause
        )

    result = score_cases([case], evaluator=stub)
    assert result.true_positives == 2
    assert result.root_cause_considered == 2
    assert result.root_cause_matches == 1
    assert result.root_cause_accuracy == Decimal("0.5")


def test_dollar_accuracy_is_detected_over_injected() -> None:
    case = _mini_case(
        "c1",
        (
            ExpectedFinding(
                0, "P", Money("100.00"), RootCause.UNDETERMINED_VARIANCE, Money("100.00")
            ),
        ),
    )
    result = score_cases(
        [case], evaluator=lambda c: (_finding(0, RootCause.UNDETERMINED_VARIANCE, Money("80.00")),)
    )
    assert result.injected_dollars == Money("100.00")
    assert result.detected_dollars == Money("80.00")
    assert result.dollar_accuracy == Decimal("0.8")


def test_excluded_from_scoring_cases_are_skipped_without_calling_the_evaluator() -> None:
    case = _mini_case("reversal-0", (), excluded=True)

    def explode(c: GoldenCase) -> tuple[Finding, ...]:
        raise AssertionError("evaluator must not be called for excluded_from_scoring cases")

    result = score_cases([case], evaluator=explode)
    assert result.total_lines == 0


def test_mismatched_finding_count_raises() -> None:
    case = _mini_case(
        "c1",
        (ExpectedFinding(0, "P", Money("100.00"), RootCause.CORRECT_NO_VARIANCE, Money.zero()),),
    )
    try:
        score_cases([case], evaluator=lambda c: ())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on finding-count mismatch")


# --- Integration: the real pipeline against the golden set -------------------


def test_main_passes_against_the_real_golden_dataset() -> None:
    exit_code = main()
    assert exit_code == 0


def test_evaluate_case_runs_the_real_pipeline_for_a_single_case() -> None:
    findings = evaluate_case(GOLDEN_CASES[0])
    assert len(findings) == len(GOLDEN_CASES[0].expected_findings)


# --- The regression-proof gate: breaking variance.py must fail the eval ------
#
# Rather than reaching into evals.run's module internals, this drives the
# same `evaluator` seam score_cases() takes in production (evaluate_case is
# the default): it runs the real pipeline via evaluate_case, then simulates
# the implant-carve-out rule in variance.py silently failing by rewriting
# every IMPLANT_NOT_CARVED_OUT finding to CORRECT_NO_VARIANCE. This is the
# literal proof required by the Phase 2 gate: a broken rule must drop
# recall below the threshold and fail the run.


def _implant_rule_disabled(case: GoldenCase) -> tuple[Finding, ...]:
    findings = evaluate_case(case)
    patched = []
    for f in findings:
        if f.root_cause == RootCause.IMPLANT_NOT_CARVED_OUT:
            patched.append(
                Finding(
                    claim_id=f.claim_id,
                    line_index=f.line_index,
                    procedure_code=f.procedure_code,
                    expected_allowed=f.expected_allowed,
                    actual_allowed=f.actual_allowed,
                    shortfall=Money.zero(),
                    root_cause=RootCause.CORRECT_NO_VARIANCE,
                    evidence=f.evidence,
                )
            )
        else:
            patched.append(f)
    return tuple(patched)


def test_breaking_a_variance_rule_fails_the_recall_gate() -> None:
    baseline = score_cases(GOLDEN_CASES)
    assert baseline.passed

    broken = score_cases(GOLDEN_CASES, evaluator=_implant_rule_disabled)

    assert broken.recall < Decimal("1")
    assert not broken.passed
