"""Synthetic X12 835 generator for the Phase 2 eval harness.

Ground truth for every generated line is derived independently of
domain.variance.evaluate_claim -- the function this harness exists to grade:

- `expected_allowed` comes from domain.contract.price_claim, a pricing oracle
  that Phase 1 already unit-tests exhaustively. Reusing it here is not
  circular: price_claim is not the thing under test.
- `actual_allowed` (what the payer's remittance shows) comes from this
  module's own injection arithmetic -- e.g. "apply the contract's
  second_procedure_rate to the primary line", reading the rate straight off
  the ContractVersion, never by calling price_claim's own ranking logic.
- The expected RootCause is assigned from the shape of the injection, not by
  invoking evaluate_claim. An injection that doesn't change anything on a
  given claim shape (e.g. dropping a bilateral modifier on a claim with no
  modifier-50 line) is recorded as CORRECT_NO_VARIANCE, not the intended
  defect -- ground truth reflects what actually happened.

`build_golden_cases()` is pure. `write_golden_dataset()` / `main()` render
the result to evals/golden/cases.py as literal Python, which is committed
and becomes the frozen regression baseline -- evals/run.py only ever imports
that file, it never re-invokes this generator.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, is_dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    ClaimLineInput,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
    StopLossRule,
    price_claim,
)
from domain.money import Money, Rate
from domain.variance import RootCause

# --- Low-level X12 835 segment builders --------------------------------------
#
# Self-contained (not shared with tests/domain/fixtures_x835.py) -- eval
# tooling is a second, independent producer of wire-format text, and
# depending on test-only code from non-test code is backwards.

_ELEMENT_SEP = "*"
_SUB_ELEMENT_SEP = ":"
_SEGMENT_TERM = "~"


@dataclass(frozen=True, slots=True)
class WireLine:
    """One SVC line as it will appear in the payer's remittance text."""

    procedure_code: str
    modifiers: tuple[str, ...]
    charge: Money
    actual_allowed: Money
    service_date: date


def _seg(*fields: str) -> str:
    return _ELEMENT_SEP.join(fields)


def _isa_segment() -> str:
    fields = [
        "ISA", "00", "          ", "00", "          ",
        "ZZ", "SENDERID       ", "ZZ", "RECEIVERID     ",
        "230101", "1200", "^", "00501", "000000905", "0", "T", _SUB_ELEMENT_SEP,
    ]
    return _ELEMENT_SEP.join(fields)


def _assemble(segments: list[str]) -> str:
    return _SEGMENT_TERM.join(segments) + _SEGMENT_TERM


def _envelope_head(control_number: str, bpr_total_paid: Money) -> list[str]:
    return [
        _isa_segment(),
        _seg("GS", "HP", "SENDERID", "RECEIVERID", "20230101", "1200", "1", "X", "005010X221A1"),
        _seg("ST", "835", control_number, "005010X221A1"),
        _seg(
            "BPR", "I", str(bpr_total_paid), "C", "ACH", "CTX", "01", "999999999",
            "DA", "123456789", "1999999999", "", "", "01", "999999998", "DA",
            "987654321", "20230115",
        ),
        _seg("TRN", "1", f"TRACE{control_number}", "1999999999"),
        _seg("N1", "PR", "EVAL PAYER"),
        _seg("N1", "PE", "EVAL ASC", "XX", "1999999999"),
    ]


def _envelope_tail(control_number: str, segment_count: int) -> list[str]:
    return [
        _seg("SE", str(segment_count), control_number),
        _seg("GE", "1", "1"),
        _seg("IEA", "1", "000000905"),
    ]


def _line_segments(line: WireLine) -> list[str]:
    composite = _SUB_ELEMENT_SEP.join(("HC", line.procedure_code, *line.modifiers))
    co_amount = line.charge - line.actual_allowed
    segs = [
        _seg("SVC", composite, str(line.charge), str(line.actual_allowed), "", "1"),
        _seg("DTM", "472", line.service_date.strftime("%Y%m%d")),
    ]
    if co_amount != Money.zero():
        segs.append(_seg("CAS", "CO", "45", str(co_amount)))
    return segs


def _claim_segments(
    *,
    patient_control_number: str,
    claim_status: str,
    total_charge: Money,
    total_paid: Money,
    payer_claim_control: str,
    member_id: str,
    lines: Sequence[WireLine],
    claim_level_cas: tuple[tuple[str, str, Money], ...] = (),
) -> list[str]:
    segs = [
        _seg("LX", "1"),
        _seg(
            "CLP", patient_control_number, claim_status, str(total_charge),
            str(total_paid), "0.00", "12", payer_claim_control, "11",
        ),
        _seg("NM1", "QC", "1", "EVAL PATIENT", "SYNTHETIC", "", "", "", "MI", member_id),
        _seg("NM1", "82", "2", "EVAL RENDERING PROVIDER", "", "", "", "", "XX", "1999999999"),
        _seg("DTM", "232", lines[0].service_date.strftime("%Y%m%d")),
    ]
    for group, reason, amount in claim_level_cas:
        segs.append(_seg("CAS", group, reason, str(amount)))
    for line in lines:
        segs.extend(_line_segments(line))
    return segs


def _build_transaction(
    *, control_number: str, bpr_total_paid: Money, claim_segments: list[str]
) -> str:
    segments = [
        *_envelope_head(control_number, bpr_total_paid),
        *claim_segments,
        *_envelope_tail(control_number, segment_count=len(claim_segments) + 2),
    ]
    return _assemble(segments)


# --- Id / date / money variation helpers -------------------------------------

_BASE_DATE = date(2023, 2, 1)


def _next_ids(counter: Iterator[int]) -> tuple[str, str, str]:
    n = next(counter)
    return f"CLAIM{n:06d}", f"TESTMBR{n:06d}", f"PAYERCTRL{n:06d}"


def _vary_date(rng: random.Random, spread_days: int = 300) -> date:
    return _BASE_DATE + timedelta(days=rng.randrange(spread_days))


def _jitter_money(rng: random.Random, base: Money, lo_pct: int, hi_pct: int) -> Money:
    pct = Decimal(rng.randint(lo_pct, hi_pct))
    return base.times(Rate((Decimal(100) + pct) / Decimal(100)))


def _bill_charge(rng: random.Random, allowed: Money) -> Money:
    pct = Decimal(rng.randint(115, 145))
    return allowed.times(Rate(pct / Decimal(100)))


def _bill_charge_below(rng: random.Random, allowed: Money) -> Money:
    """Phase 8: the opposite of _bill_charge -- a claim billed *below* its
    fee-schedule amount, the scenario lesser-of pricing exists for."""
    pct = Decimal(rng.randint(60, 90))
    return allowed.times(Rate(pct / Decimal(100)))


# --- Contract library ---------------------------------------------------------

_PAYER_ID = "EVALPAYER1"
_IMPLANT_CODE = "L8699"
_UNPRICED_CODE = "99199"


def _fee_schedule() -> dict[str, Money]:
    return {
        "99213": Money("100.00"),
        "99214": Money("150.00"),
        "99215": Money("200.00"),
        "20610": Money("250.00"),
        "27447": Money("5000.00"),
        "27446": Money("4000.00"),
        "29881": Money("2200.00"),
        "64483": Money("1800.00"),
        "43239": Money("1600.00"),
        "45380": Money("2100.00"),
    }


_PROCEDURE_CODES = tuple(_fee_schedule().keys())


def _inert_stop_loss_rule() -> StopLossRule:
    return StopLossRule(
        enabled=False,
        threshold=Money.zero(),
        outlier_rate=Rate.percent(Decimal("0")),
        first_dollar=True,
    )


def _make_contract(
    *,
    effective_from: date,
    effective_to: date | None = None,
    fee_schedule: dict[str, Money] | None = None,
    percent_of_charge_rate: Rate | None = None,
    mppr_enabled: bool = True,
    # Phase 8 (docs/MASTER-BUILD-PROMPT-V2.md): both default to their
    # pre-Phase-8 inert state -- False / a disabled rule -- so every one
    # of the ~448 pre-existing golden cases built via this factory is
    # provably unaffected by these two new mandatory ContractVersion
    # fields. Only the new lesser-of/stop-loss builders opt in.
    lesser_of_charge_enabled: bool = False,
    stop_loss_rule: StopLossRule | None = None,
) -> ContractVersion:
    return ContractVersion(
        payer_id=_PAYER_ID,
        effective_from=effective_from,
        effective_to=effective_to,
        default_pricing_method=PricingMethod.FEE_SCHEDULE,
        fee_schedule=fee_schedule if fee_schedule is not None else _fee_schedule(),
        percent_of_charge_rate=percent_of_charge_rate,
        case_rate_groups=(),
        mppr_rule=MPPRRule(
            enabled=mppr_enabled,
            second_procedure_rate=Rate.percent(Decimal("50")),
            third_and_subsequent_rate=Rate.percent(Decimal("25")),
            exempt_codes=frozenset(),
        ),
        bilateral_rule=BilateralRule(
            enabled=True,
            total_rate=Rate.percent(Decimal("150")),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
        assistant_surgeon_rule=AssistantSurgeonRule(
            enabled=True,
            rate=Rate.percent(Decimal("16")),
            applicable_modifiers=frozenset({"80", "81", "82", "AS"}),
        ),
        implant_carveout_rule=ImplantCarveoutRule(
            enabled=True,
            procedure_codes=frozenset({_IMPLANT_CODE}),
            revenue_codes=frozenset({"0278"}),
        ),
        lesser_of_charge_enabled=lesser_of_charge_enabled,
        stop_loss_rule=stop_loss_rule if stop_loss_rule is not None else _inert_stop_loss_rule(),
    )


_CONTRACT_CURRENT = _make_contract(effective_from=date(2023, 1, 1))
_CONTRACT_NO_MPPR = _make_contract(effective_from=date(2023, 1, 1), mppr_enabled=False)
_CONTRACT_PERCENT = _make_contract(
    effective_from=date(2023, 1, 1),
    fee_schedule={},
    percent_of_charge_rate=Rate.percent(Decimal("60")),
)
_OLD_FACTOR = Rate("0.85")
_CONTRACT_STALE_OLD = _make_contract(
    effective_from=date(2022, 1, 1),
    effective_to=date(2022, 12, 31),
    fee_schedule={code: amount.times(_OLD_FACTOR) for code, amount in _fee_schedule().items()},
)
_CONTRACT_STALE_NEW = _CONTRACT_CURRENT
_CONTRACT_LESSER_OF = _make_contract(
    effective_from=date(2023, 1, 1), lesser_of_charge_enabled=True
)
_STOP_LOSS_THRESHOLD = Money("10000.00")
_CONTRACT_STOP_LOSS = _make_contract(
    effective_from=date(2023, 1, 1),
    stop_loss_rule=StopLossRule(
        enabled=True,
        threshold=_STOP_LOSS_THRESHOLD,
        outlier_rate=Rate.percent(Decimal("35")),
        first_dollar=True,
    ),
)

_SHARED_CONTRACTS: dict[str, ContractVersion] = {
    "_CONTRACT_CURRENT": _CONTRACT_CURRENT,
    "_CONTRACT_NO_MPPR": _CONTRACT_NO_MPPR,
    "_CONTRACT_PERCENT": _CONTRACT_PERCENT,
    "_CONTRACT_STALE_OLD": _CONTRACT_STALE_OLD,
    "_CONTRACT_STALE_NEW": _CONTRACT_STALE_NEW,
    "_CONTRACT_LESSER_OF": _CONTRACT_LESSER_OF,
    "_CONTRACT_STOP_LOSS": _CONTRACT_STOP_LOSS,
}


# --- Case types ----------------------------------------------------------------


class DefectType(enum.Enum):
    IMPLANT_CARVEOUT_IGNORED = "implant_carveout_ignored"
    MPPR_APPLIED_TO_PRIMARY = "mppr_applied_to_primary"
    BILATERAL_MODIFIER_DROPPED = "bilateral_modifier_dropped"
    STALE_FEE_SCHEDULE = "stale_fee_schedule"
    DUPLICATE_LINE = "duplicate_line"
    REVERSAL_AFTER_PAYMENT = "reversal_after_payment"
    SECONDARY_PAYER_UNDERPAYMENT = "secondary_payer_underpayment"
    UNPRICED_CODE = "unpriced_code"
    STOP_LOSS_NOT_APPLIED = "stop_loss_not_applied"
    CORRECT_PAYMENT = "correct_payment"


@dataclass(frozen=True, slots=True)
class ExpectedFinding:
    line_index: int
    procedure_code: str
    expected_allowed: Money | None
    root_cause: RootCause
    shortfall: Money


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    defect_type: DefectType
    x835_text: str
    patient_control_number: str
    payer_id: str
    date_of_service: date
    claim_lines: tuple[ClaimLineInput, ...]
    contract_versions: tuple[ContractVersion, ...]
    expected_findings: tuple[ExpectedFinding, ...]
    excluded_from_scoring: bool = False


_TARGET_PER_CATEGORY = 56
_GENERATOR_SEED = 835201


# --- Category builders ---------------------------------------------------------
#
# Each builder constructs the "expected" side by calling price_claim (the
# trusted Phase 1 oracle) and the "actual" side by hand, per the ground-truth
# methodology in the module docstring.


def _build_implant_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for i in range(_TARGET_PER_CATEGORY):
        invoice_cost = _jitter_money(rng, Money("2000.00"), -25, 25)
        charge = invoice_cost.times(Rate("1.3"))
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(_IMPLANT_CODE, (), None, charge, invoice_cost, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_CURRENT)
        exp0 = expected.lines[0]
        assert exp0.allowed is not None
        actual_allowed = charge.times(Rate.percent(Decimal("60")))
        wire_line = WireLine(_IMPLANT_CODE, (), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"implant-{i:04d}",
                defect_type=DefectType.IMPLANT_CARVEOUT_IGNORED,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_CURRENT,),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        _IMPLANT_CODE,
                        exp0.allowed,
                        RootCause.IMPLANT_NOT_CARVED_OUT,
                        exp0.allowed - actual_allowed,
                    ),
                ),
            )
        )
    return cases


def _build_mppr_primary_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        code_a, code_b = rng.sample(_PROCEDURE_CODES, 2)
        code_hi, code_lo = sorted((code_a, code_b), key=lambda c: fee[c], reverse=True)
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        charge_hi = _bill_charge(rng, fee[code_hi])
        charge_lo = _bill_charge(rng, fee[code_lo])
        lines = (
            ClaimLineInput(code_hi, (), None, charge_hi, None, Decimal("1")),
            ClaimLineInput(code_lo, (), None, charge_lo, None, Decimal("1")),
        )
        expected = price_claim(lines, _CONTRACT_CURRENT)
        exp0, exp1 = expected.lines
        assert exp0.allowed is not None
        assert exp1.allowed is not None
        actual0 = exp0.allowed.times(_CONTRACT_CURRENT.mppr_rule.second_procedure_rate)
        actual1 = exp1.allowed
        wire_lines = (
            WireLine(code_hi, (), charge_hi, actual0, svc_date),
            WireLine(code_lo, (), charge_lo, actual1, svc_date),
        )
        total_charge = charge_hi + charge_lo
        total_paid = actual0 + actual1
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=total_paid,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=total_charge,
                total_paid=total_paid,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=wire_lines,
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"mppr-primary-{i:04d}",
                defect_type=DefectType.MPPR_APPLIED_TO_PRIMARY,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=lines,
                contract_versions=(_CONTRACT_CURRENT,),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        code_hi,
                        exp0.allowed,
                        RootCause.UNDETERMINED_VARIANCE,
                        exp0.allowed - actual0,
                    ),
                    ExpectedFinding(
                        1,
                        code_lo,
                        exp1.allowed,
                        RootCause.CORRECT_NO_VARIANCE,
                        exp1.allowed - actual1,
                    ),
                ),
            )
        )
    return cases


def _build_bilateral_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        code = rng.choice(_PROCEDURE_CODES)
        base = fee[code]
        charge = _bill_charge(rng, base.times(Rate.percent(Decimal("150"))))
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(code, ("50",), None, charge, None, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_CURRENT)
        exp0 = expected.lines[0]
        assert exp0.allowed is not None
        actual_allowed = base
        wire_line = WireLine(code, ("50",), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"bilateral-{i:04d}",
                defect_type=DefectType.BILATERAL_MODIFIER_DROPPED,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_CURRENT,),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        code,
                        exp0.allowed,
                        RootCause.BILATERAL_MODIFIER_DROPPED,
                        exp0.allowed - actual_allowed,
                    ),
                ),
            )
        )
    return cases


def _build_stale_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    old_fee = _CONTRACT_STALE_OLD.fee_schedule
    for i in range(_TARGET_PER_CATEGORY):
        code = rng.choice(_PROCEDURE_CODES)
        charge = _bill_charge(rng, fee[code])
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(code, (), None, charge, None, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_STALE_NEW)
        exp0 = expected.lines[0]
        assert exp0.allowed is not None
        actual_allowed = old_fee[code]
        wire_line = WireLine(code, (), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"stale-{i:04d}",
                defect_type=DefectType.STALE_FEE_SCHEDULE,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_STALE_OLD, _CONTRACT_STALE_NEW),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        code,
                        exp0.allowed,
                        RootCause.STALE_FEE_SCHEDULE,
                        exp0.allowed - actual_allowed,
                    ),
                ),
            )
        )
    return cases


def _build_duplicate_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        code = rng.choice(_PROCEDURE_CODES)
        charge = _bill_charge(rng, fee[code])
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        lines = (
            ClaimLineInput(code, (), None, charge, None, Decimal("1")),
            ClaimLineInput(code, (), None, charge, None, Decimal("1")),
        )
        expected = price_claim(lines, _CONTRACT_NO_MPPR)
        exp0, exp1 = expected.lines
        assert exp0.allowed is not None
        assert exp1.allowed is not None
        wire_lines = (
            WireLine(code, (), charge, exp0.allowed, svc_date),
            WireLine(code, (), charge, exp1.allowed, svc_date),
        )
        total_charge = charge + charge
        total_paid = exp0.allowed + exp1.allowed
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=total_paid,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=total_charge,
                total_paid=total_paid,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=wire_lines,
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"duplicate-{i:04d}",
                defect_type=DefectType.DUPLICATE_LINE,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=lines,
                contract_versions=(_CONTRACT_NO_MPPR,),
                expected_findings=(
                    ExpectedFinding(
                        0, code, exp0.allowed, RootCause.CORRECT_NO_VARIANCE, Money.zero()
                    ),
                    ExpectedFinding(1, code, exp1.allowed, RootCause.DUPLICATE_LINE, Money.zero()),
                ),
            )
        )
    return cases


def _build_reversal_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        code = rng.choice(_PROCEDURE_CODES)
        charge = _bill_charge(rng, fee[code])
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        original_paid = fee[code]
        wire_line = WireLine(code, (), charge, original_paid, svc_date)
        claim_segs = _claim_segments(
            patient_control_number=pcn,
            claim_status="22",
            total_charge=charge,
            total_paid=-original_paid,
            payer_claim_control=payer_claim,
            member_id=member_id,
            lines=(wire_line,),
        )
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=-original_paid,
            claim_segments=claim_segs,
        )
        cases.append(
            GoldenCase(
                case_id=f"reversal-{i:04d}",
                defect_type=DefectType.REVERSAL_AFTER_PAYMENT,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(),
                contract_versions=(_CONTRACT_CURRENT,),
                expected_findings=(),
                excluded_from_scoring=True,
            )
        )
    return cases


def _build_secondary_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        code = rng.choice(_PROCEDURE_CODES)
        charge = _bill_charge(rng, fee[code])
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(code, (), None, charge, None, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_NO_MPPR)
        exp0 = expected.lines[0]
        assert exp0.allowed is not None
        shortfall_amt = _jitter_money(rng, exp0.allowed.times(Rate.percent(Decimal("20"))), -30, 30)
        actual_allowed = exp0.allowed - shortfall_amt
        primary_oa = _jitter_money(rng, exp0.allowed.times(Rate.percent(Decimal("15"))), -10, 10)
        wire_line = WireLine(code, (), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="2",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
                claim_level_cas=(("OA", "23", primary_oa),),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"secondary-{i:04d}",
                defect_type=DefectType.SECONDARY_PAYER_UNDERPAYMENT,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_NO_MPPR,),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        code,
                        exp0.allowed,
                        RootCause.UNDETERMINED_VARIANCE,
                        exp0.allowed - actual_allowed,
                    ),
                ),
            )
        )
    return cases


def _build_unpriced_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for i in range(_TARGET_PER_CATEGORY):
        charge = _jitter_money(rng, Money("300.00"), -20, 40)
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(_UNPRICED_CODE, (), None, charge, None, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_NO_MPPR)
        exp0 = expected.lines[0]
        assert exp0.allowed is None
        actual_allowed = charge.times(Rate.percent(Decimal("50")))
        wire_line = WireLine(_UNPRICED_CODE, (), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"unpriced-{i:04d}",
                defect_type=DefectType.UNPRICED_CODE,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_NO_MPPR,),
                expected_findings=(
                    ExpectedFinding(0, _UNPRICED_CODE, None, RootCause.UNPRICED_CODE, Money.zero()),
                ),
            )
        )
    return cases


def _build_stop_loss_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    """Phase 8: total charges above the outlier threshold, paid at the
    flat fee-schedule rate instead of the stop-loss percentage of billed
    charges. `code`/`flat_fee` are fixed (not randomly chosen) so the
    "actual" side is always genuinely underpaid relative to the outlier
    basis regardless of the charge jitter below -- picking a random,
    possibly high-value procedure code risked an occasional overpayment
    case sneaking into a category meant to be a pure underpayment defect."""
    cases: list[GoldenCase] = []
    code = "99213"
    flat_fee = _fee_schedule()[code]
    for i in range(_TARGET_PER_CATEGORY):
        charge = _jitter_money(rng, Money("15000.00"), -20, 20)
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        line = ClaimLineInput(code, (), None, charge, None, Decimal("1"))
        expected = price_claim((line,), _CONTRACT_STOP_LOSS)
        exp0 = expected.lines[0]
        assert exp0.allowed is not None
        actual_allowed = flat_fee
        wire_line = WireLine(code, (), charge, actual_allowed, svc_date)
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=actual_allowed,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=charge,
                total_paid=actual_allowed,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=(wire_line,),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"stoploss-{i:04d}",
                defect_type=DefectType.STOP_LOSS_NOT_APPLIED,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=(line,),
                contract_versions=(_CONTRACT_STOP_LOSS,),
                expected_findings=(
                    ExpectedFinding(
                        0,
                        code,
                        exp0.allowed,
                        RootCause.STOP_LOSS_NOT_APPLIED,
                        exp0.allowed - actual_allowed,
                    ),
                ),
            )
        )
    return cases


def _build_correct_cases(rng: random.Random, counter: Iterator[int]) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    fee = _fee_schedule()
    for i in range(_TARGET_PER_CATEGORY):
        pattern = i % 7
        svc_date = _vary_date(rng)
        pcn, member_id, payer_claim = _next_ids(counter)
        contract: ContractVersion
        lines: tuple[ClaimLineInput, ...]
        if pattern == 0:  # plain fee schedule
            code = rng.choice(_PROCEDURE_CODES)
            charge = _bill_charge(rng, fee[code])
            lines = (ClaimLineInput(code, (), None, charge, None, Decimal("1")),)
            contract = _CONTRACT_NO_MPPR
        elif pattern == 1:  # percent of charge
            code = rng.choice(_PROCEDURE_CODES)
            charge = _jitter_money(rng, Money("400.00"), -20, 60)
            lines = (ClaimLineInput(code, (), None, charge, None, Decimal("1")),)
            contract = _CONTRACT_PERCENT
        elif pattern == 2:  # correctly-applied MPPR, two lines
            code_a, code_b = rng.sample(_PROCEDURE_CODES, 2)
            code_hi, code_lo = sorted((code_a, code_b), key=lambda c: fee[c], reverse=True)
            charge_hi = _bill_charge(rng, fee[code_hi])
            charge_lo = _bill_charge(rng, fee[code_lo])
            lines = (
                ClaimLineInput(code_hi, (), None, charge_hi, None, Decimal("1")),
                ClaimLineInput(code_lo, (), None, charge_lo, None, Decimal("1")),
            )
            contract = _CONTRACT_CURRENT
        elif pattern == 3:  # correctly-applied bilateral
            code = rng.choice(_PROCEDURE_CODES)
            charge = _bill_charge(rng, fee[code].times(Rate.percent(Decimal("150"))))
            lines = (ClaimLineInput(code, ("50",), None, charge, None, Decimal("1")),)
            contract = _CONTRACT_CURRENT
        elif pattern == 4:  # correctly carved-out implant
            invoice_cost = _jitter_money(rng, Money("2000.00"), -25, 25)
            charge = invoice_cost.times(Rate("1.3"))
            lines = (ClaimLineInput(_IMPLANT_CODE, (), None, charge, invoice_cost, Decimal("1")),)
            contract = _CONTRACT_CURRENT
        elif pattern == 5:  # Phase 8: lesser-of, billed below fee schedule
            code = rng.choice(_PROCEDURE_CODES)
            charge = _bill_charge_below(rng, fee[code])
            lines = (ClaimLineInput(code, (), None, charge, None, Decimal("1")),)
            contract = _CONTRACT_LESSER_OF
        else:  # Phase 8: stop-loss, correctly paid at the outlier percentage
            code = "99213"
            charge = _jitter_money(rng, Money("15000.00"), -20, 20)
            lines = (ClaimLineInput(code, (), None, charge, None, Decimal("1")),)
            contract = _CONTRACT_STOP_LOSS

        expected = price_claim(lines, contract)
        wire_lines: list[WireLine] = []
        findings: list[ExpectedFinding] = []
        for idx, exp_line in enumerate(expected.lines):
            assert exp_line.allowed is not None
            wire_lines.append(
                WireLine(
                    exp_line.procedure_code,
                    lines[idx].modifiers,
                    lines[idx].charge,
                    exp_line.allowed,
                    svc_date,
                )
            )
            findings.append(
                ExpectedFinding(
                    idx,
                    exp_line.procedure_code,
                    exp_line.allowed,
                    RootCause.CORRECT_NO_VARIANCE,
                    Money.zero(),
                )
            )
        total_charge = sum((wl.charge for wl in wire_lines), Money.zero())
        total_paid = sum((wl.actual_allowed for wl in wire_lines), Money.zero())
        text = _build_transaction(
            control_number=pcn.removeprefix("CLAIM"),
            bpr_total_paid=total_paid,
            claim_segments=_claim_segments(
                patient_control_number=pcn,
                claim_status="1",
                total_charge=total_charge,
                total_paid=total_paid,
                payer_claim_control=payer_claim,
                member_id=member_id,
                lines=tuple(wire_lines),
            ),
        )
        cases.append(
            GoldenCase(
                case_id=f"correct-{i:04d}",
                defect_type=DefectType.CORRECT_PAYMENT,
                x835_text=text,
                patient_control_number=pcn,
                payer_id=_PAYER_ID,
                date_of_service=svc_date,
                claim_lines=lines,
                contract_versions=(contract,),
                expected_findings=tuple(findings),
            )
        )
    return cases


def build_golden_cases(seed: int = _GENERATOR_SEED) -> tuple[GoldenCase, ...]:
    rng = random.Random(seed)
    counter = itertools.count(1)
    builders = (
        _build_implant_cases,
        _build_mppr_primary_cases,
        _build_bilateral_cases,
        _build_stale_cases,
        _build_duplicate_cases,
        _build_reversal_cases,
        _build_secondary_cases,
        _build_unpriced_cases,
        _build_stop_loss_cases,
        _build_correct_cases,
    )
    cases: list[GoldenCase] = []
    for builder in builders:
        cases.extend(builder(rng, counter))
    return tuple(cases)


# --- Rendering the frozen dataset ----------------------------------------------
#
# Dataclasses' default __repr__ calls repr() on every field, which produces
# invalid Python for enum members (`<DefectType.X: 'x'>`). This renderer
# special-cases enums (and a handful of other types) and is otherwise a
# faithful, re-parseable literal form -- so evals/golden/cases.py is plain,
# reviewable Python, not a bespoke serialization format.


def _render(value: object, shared: dict[int, str] | None = None) -> str:
    if shared is not None and id(value) in shared:
        return shared[id(value)]
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, Money | Rate):
        return repr(value)
    if isinstance(value, Decimal):
        return f"Decimal('{value}')"
    if isinstance(value, date):
        return f"date({value.year}, {value.month}, {value.day})"
    if value is None or isinstance(value, str | bool | int):
        return repr(value)
    if isinstance(value, frozenset):
        if not value:
            return "frozenset()"
        inner = ", ".join(_render(v, shared) for v in sorted(value))
        return f"frozenset({{{inner}}})"
    if isinstance(value, tuple):
        if not value:
            return "()"
        inner = ", ".join(_render(v, shared) for v in value)
        return f"({inner},)" if len(value) == 1 else f"({inner})"
    if isinstance(value, dict):
        inner = ", ".join(f"{_render(k, shared)}: {_render(v, shared)}" for k, v in value.items())
        return f"{{{inner}}}"
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value).__name__
        field_strs = ", ".join(
            f"{f.name}={_render(getattr(value, f.name), shared)}" for f in dataclasses.fields(value)
        )
        return f"{cls}({field_strs})"
    raise TypeError(f"_render: no rendering rule for {type(value).__name__}")


def write_golden_dataset(path: Path) -> None:
    cases = build_golden_cases()
    shared_by_id = {id(v): k for k, v in _SHARED_CONTRACTS.items()}
    lines: list[str] = [
        '"""GENERATED by `python -m evals.generator`. Do not hand-edit.',
        "",
        "Frozen regression baseline for the Phase 2 eval harness. See",
        "docs/MASTER-BUILD-PROMPT.md (Phase 2) and evals/generator.py.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from datetime import date",
        "from decimal import Decimal",
        "",
        "from domain.contract import (",
        "    AssistantSurgeonRule,",
        "    BilateralConvention,",
        "    BilateralRule,",
        "    ClaimLineInput,",
        "    ContractVersion,",
        "    ImplantCarveoutRule,",
        "    MPPRRule,",
        "    PricingMethod,",
        "    StopLossRule,",
        ")",
        "from domain.money import Money, Rate",
        "from domain.variance import RootCause",
        "from evals.generator import DefectType, ExpectedFinding, GoldenCase",
        "",
    ]
    for name, contract in _SHARED_CONTRACTS.items():
        lines.append(f"{name} = {_render(contract)}")
        lines.append("")
    lines.append("GOLDEN_CASES: tuple[GoldenCase, ...] = (")
    for case in cases:
        lines.append(f"    {_render(case, shared_by_id)},")
    lines.append(")")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    output = Path(__file__).parent / "golden" / "cases.py"
    write_golden_dataset(output)
    print(f"wrote {len(build_golden_cases())} golden cases to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
