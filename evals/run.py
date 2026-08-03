"""Phase 2 eval harness: runs the real domain pipeline against the frozen
golden dataset and reports recall, precision, root-cause accuracy, and
dollar accuracy.

`evals/golden/cases.py` is committed, frozen output of `evals/generator.py`
-- this module only ever imports GOLDEN_CASES, it never regenerates it.
That is what makes "deliberately breaking one rule in variance.py makes the
eval fail" a meaningful gate: the expected findings this module compares
against don't move when variance.py changes, only the pipeline's live
output does.

`make eval` runs `python -m evals.run`, which fails the build (non-zero
exit) if recall < 100% or precision < 98%, per docs/MASTER-BUILD-PROMPT.md.
Root-cause and dollar accuracy are always reported but don't gate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from domain.contract import find_effective_contract, price_claim
from domain.money import Money
from domain.variance import ActualServiceLine, Finding, RootCause, evaluate_claim
from domain.x835 import parse_835
from evals.generator import GoldenCase
from evals.golden.cases import GOLDEN_CASES

_TOLERANCE = Money("0.01")
_RECALL_GATE = Decimal("1.0")
_PRECISION_GATE = Decimal("0.98")


def evaluate_case(case: GoldenCase) -> tuple[Finding, ...]:
    """Runs the real pipeline (parse -> price -> evaluate) for one case."""
    result = parse_835(case.x835_text)
    claim = next(
        (
            c
            for c in result.transactions[0].claims
            if c.patient_control_number == case.patient_control_number
        ),
        None,
    )
    if claim is None:
        raise ValueError(f"{case.case_id}: parsed 835 does not contain its own claim")
    actual_lines = tuple(
        ActualServiceLine(sl.procedure_code, sl.service_date, sl.charge, sl.allowed)
        for sl in claim.service_lines
    )
    contract = find_effective_contract(case.payer_id, case.date_of_service, case.contract_versions)
    if contract is None:
        raise ValueError(f"{case.case_id}: no effective contract for its own date of service")
    expected = price_claim(case.claim_lines, contract)
    return evaluate_claim(
        case.case_id, expected, actual_lines, all_contract_versions=case.contract_versions
    )


@dataclass(frozen=True, slots=True)
class ScoreResult:
    true_positives: int = 0
    false_negatives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    root_cause_matches: int = 0
    root_cause_considered: int = 0
    detected_dollars: Money = field(default_factory=Money.zero)
    injected_dollars: Money = field(default_factory=Money.zero)

    @property
    def total_lines(self) -> int:
        return (
            self.true_positives + self.false_negatives + self.false_positives + self.true_negatives
        )

    @property
    def recall(self) -> Decimal:
        denom = self.true_positives + self.false_negatives
        if denom == 0:
            return Decimal("1")
        return Decimal(self.true_positives) / Decimal(denom)

    @property
    def precision(self) -> Decimal:
        denom = self.true_positives + self.false_positives
        if denom == 0:
            return Decimal("1")
        return Decimal(self.true_positives) / Decimal(denom)

    @property
    def root_cause_accuracy(self) -> Decimal:
        if self.root_cause_considered == 0:
            return Decimal("1")
        return Decimal(self.root_cause_matches) / Decimal(self.root_cause_considered)

    @property
    def dollar_accuracy(self) -> Decimal:
        if self.injected_dollars == Money.zero():
            return Decimal("1")
        return self.detected_dollars.as_decimal() / self.injected_dollars.as_decimal()

    @property
    def passed(self) -> bool:
        return self.recall >= _RECALL_GATE and self.precision >= _PRECISION_GATE


def score_cases(
    cases: Sequence[GoldenCase],
    evaluator: Callable[[GoldenCase], tuple[Finding, ...]] = evaluate_case,
) -> ScoreResult:
    tp = fn = fp = tn = 0
    root_matches = root_considered = 0
    detected = Money.zero()
    injected = Money.zero()

    for case in cases:
        if case.excluded_from_scoring:
            # Still parsed by the caller's evaluator-independent generator
            # tests; a reversal/denial claim contributes no scoring rows
            # here because per-claim netting is Phase 5 (ingestion) work --
            # see docs/MASTER-BUILD-PROMPT.md Phase 2 plan.
            continue

        findings = evaluator(case)
        if len(findings) != len(case.expected_findings):
            raise ValueError(
                f"{case.case_id}: pipeline produced {len(findings)} findings, "
                f"expected {len(case.expected_findings)}"
            )

        for ground_truth, actual in zip(case.expected_findings, findings, strict=True):
            gt_positive = ground_truth.shortfall > _TOLERANCE
            # A line only counts as "detected" if it both carries a positive
            # shortfall AND was actually classified as a variance -- a rule
            # that computes the right dollars but mislabels the line
            # CORRECT_NO_VARIANCE must not be scored as a catch.
            pred_positive = (
                actual.shortfall > _TOLERANCE and actual.root_cause != RootCause.CORRECT_NO_VARIANCE
            )

            if gt_positive and pred_positive:
                tp += 1
            elif gt_positive:
                fn += 1
            elif pred_positive:
                fp += 1
            else:
                tn += 1

            if gt_positive and pred_positive:
                root_considered += 1
                if ground_truth.root_cause == actual.root_cause:
                    root_matches += 1

            if ground_truth.shortfall > Money.zero():
                injected = injected + ground_truth.shortfall
            if actual.shortfall > Money.zero():
                detected = detected + actual.shortfall

    return ScoreResult(
        true_positives=tp,
        false_negatives=fn,
        false_positives=fp,
        true_negatives=tn,
        root_cause_matches=root_matches,
        root_cause_considered=root_considered,
        detected_dollars=detected,
        injected_dollars=injected,
    )


def _format_pct(value: Decimal) -> str:
    return f"{value * Decimal(100):.1f}%"


def main() -> int:
    result = score_cases(GOLDEN_CASES)
    print(f"golden cases:        {len(GOLDEN_CASES)}")
    print(f"lines scored:        {result.total_lines}")
    print(f"recall:              {_format_pct(result.recall)} (gate: 100%)")
    print(f"precision:           {_format_pct(result.precision)} (gate: >= 98%)")
    print(f"root-cause accuracy: {_format_pct(result.root_cause_accuracy)}")
    print(
        f"dollar accuracy:     {_format_pct(result.dollar_accuracy)} "
        f"(detected {result.detected_dollars} vs injected {result.injected_dollars})"
    )
    print("GATE PASSED" if result.passed else "GATE FAILED")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
