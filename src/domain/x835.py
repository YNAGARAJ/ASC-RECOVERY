"""X12 835 remittance parser."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from domain.money import Money

ISA_MIN_LENGTH = 106
ISA_ELEMENT_SEP_INDEX = 3
ISA_SUB_ELEMENT_SEP_INDEX = 104
ISA_TERMINATOR_INDEX = 105


class ClaimStatus(enum.Enum):
    PRIMARY = "1"
    SECONDARY = "2"
    TERTIARY = "3"
    DENIED = "4"
    REVERSAL_OF_PREVIOUS_PAYMENT = "22"


class AdjustmentGroup(enum.Enum):
    CO = "CO"
    PR = "PR"
    OA = "OA"
    PI = "PI"
    CR = "CR"


_CLP_STATUS_BY_CODE: dict[str, ClaimStatus] = {status.value: status for status in ClaimStatus}


@dataclass(frozen=True, slots=True)
class Adjustment:
    group_code: AdjustmentGroup
    reason_code: str
    amount: Money
    quantity: Decimal | None


@dataclass(frozen=True, slots=True)
class Entity:
    entity_identifier_code: str
    name: str
    id_qualifier: str | None
    id_code: str | None


@dataclass(frozen=True, slots=True)
class DateRef:
    qualifier: str
    value: date


@dataclass(frozen=True, slots=True)
class MIA:
    raw_elements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MOA:
    raw_elements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceLine835:
    procedure_code: str
    modifiers: tuple[str, ...]
    revenue_code: str | None
    charge: Money
    paid_reported: Money
    units: Decimal
    service_date: date | None
    adjustments: tuple[Adjustment, ...]
    remarks: tuple[str, ...]
    allowed: Money
    paid_computed: Money
    raw_elements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Claim835:
    patient_control_number: str
    status: ClaimStatus
    total_charge: Money
    total_paid_reported: Money
    patient_responsibility: Money
    claim_filing_indicator_code: str
    payer_claim_control_number: str
    patient: Entity | None
    rendering_provider: Entity | None
    dates: tuple[DateRef, ...]
    adjustments: tuple[Adjustment, ...]
    mia: MIA | None
    moa: MOA | None
    remarks: tuple[str, ...]
    service_lines: tuple[ServiceLine835, ...]
    total_allowed: Money
    total_paid_computed: Money
    is_reconciled: bool
    reconciliation_diff: Money


@dataclass(frozen=True, slots=True)
class PLBAdjustment:
    provider_id: str
    fiscal_period_date: date
    reason_code: str
    reference_number: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Transaction835:
    control_number: str
    bpr_total_paid: Money
    bpr_payment_method: str
    bpr_payment_date: date | None
    trn_trace_number: str | None
    payer: Entity | None
    payee: Entity | None
    claims: tuple[Claim835, ...]
    plb_adjustments: tuple[PLBAdjustment, ...]


@dataclass(frozen=True, slots=True)
class ParseIssue:
    segment_tag: str
    segment_index: int
    reason: str
    severity: Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ParseResult:
    transactions: tuple[Transaction835, ...]
    errors: tuple[ParseIssue, ...]


# --- Internal mutable builders (not part of the public API) -----------------


@dataclass
class _LineBuilder:
    procedure_code: str
    modifiers: tuple[str, ...]
    revenue_code: str | None
    charge: Money
    paid_reported: Money
    units: Decimal
    raw_elements: tuple[str, ...]
    service_date: date | None = None
    adjustments: list[Adjustment] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)


@dataclass
class _ClaimBuilder:
    patient_control_number: str
    status: ClaimStatus
    total_charge: Money
    total_paid_reported: Money
    patient_responsibility: Money
    claim_filing_indicator_code: str
    payer_claim_control_number: str
    patient: Entity | None = None
    rendering_provider: Entity | None = None
    dates: list[DateRef] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    mia: MIA | None = None
    moa: MOA | None = None
    remarks: list[str] = field(default_factory=list)
    service_lines: list[ServiceLine835] = field(default_factory=list)
    current_line: _LineBuilder | None = None


@dataclass
class _TransactionBuilder:
    control_number: str = ""
    bpr_total_paid: Money = field(default_factory=Money.zero)
    bpr_payment_method: str = ""
    bpr_payment_date: date | None = None
    trn_trace_number: str | None = None
    payer: Entity | None = None
    payee: Entity | None = None
    claims: list[Claim835] = field(default_factory=list)
    plb_adjustments: list[PLBAdjustment] = field(default_factory=list)


_RECONCILIATION_TOLERANCE = Money("0.01")


def _parse_date(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _safe_element(elements: list[str], index: int, default: str = "") -> str:
    return elements[index] if len(elements) > index and elements[index] else default


def _parse_cas(
    elements: list[str], segment_index: int
) -> tuple[list[Adjustment], list[ParseIssue]]:
    values = elements[1:]
    adjustments: list[Adjustment] = []
    issues: list[ParseIssue] = []
    i = 0
    while i < len(values):
        chunk = values[i : i + 3]
        if len(chunk) < 3:
            issues.append(
                ParseIssue(
                    "CAS",
                    segment_index,
                    f"CAS: dangling adjustment triplet with {len(chunk)} element(s)",
                    "warning",
                )
            )
            break
        group_str, reason_code, amount_str = chunk
        try:
            group = AdjustmentGroup(group_str)
            amount = Money(amount_str)
        except (ValueError, InvalidOperation):
            issues.append(
                ParseIssue("CAS", segment_index, "CAS: invalid adjustment triplet", "warning")
            )
            i += 3
            continue
        adjustments.append(
            Adjustment(group_code=group, reason_code=reason_code, amount=amount, quantity=None)
        )
        i += 3
    return adjustments, issues


def _parse_plb(elements: list[str]) -> list[PLBAdjustment]:
    provider_id = _safe_element(elements, 1)
    fiscal_period_date = _parse_date(elements[2])
    values = elements[3:]
    result: list[PLBAdjustment] = []
    i = 0
    while i + 3 <= len(values):
        reason_code, reference_number, amount_str = values[i : i + 3]
        result.append(
            PLBAdjustment(
                provider_id=provider_id,
                fiscal_period_date=fiscal_period_date,
                reason_code=reason_code,
                reference_number=reference_number,
                amount=Money(amount_str),
            )
        )
        i += 3
    return result


def _finalize_line(line: _LineBuilder) -> ServiceLine835:
    co_total = Money.zero()
    pr_total = Money.zero()
    for adj in line.adjustments:
        if adj.group_code == AdjustmentGroup.CO:
            co_total = co_total + adj.amount
        elif adj.group_code == AdjustmentGroup.PR:
            pr_total = pr_total + adj.amount
    allowed = line.charge - co_total
    paid_computed = allowed - pr_total
    return ServiceLine835(
        procedure_code=line.procedure_code,
        modifiers=line.modifiers,
        revenue_code=line.revenue_code,
        charge=line.charge,
        paid_reported=line.paid_reported,
        units=line.units,
        service_date=line.service_date,
        adjustments=tuple(line.adjustments),
        remarks=tuple(line.remarks),
        allowed=allowed,
        paid_computed=paid_computed,
        raw_elements=line.raw_elements,
    )


def _finalize_claim(claim: _ClaimBuilder) -> Claim835:
    if claim.current_line is not None:
        claim.service_lines.append(_finalize_line(claim.current_line))
        claim.current_line = None
    total_allowed = sum((line.allowed for line in claim.service_lines), Money.zero())
    total_paid_computed = sum((line.paid_computed for line in claim.service_lines), Money.zero())
    diff = claim.total_paid_reported - total_paid_computed
    is_reconciled = abs(diff) <= _RECONCILIATION_TOLERANCE
    return Claim835(
        patient_control_number=claim.patient_control_number,
        status=claim.status,
        total_charge=claim.total_charge,
        total_paid_reported=claim.total_paid_reported,
        patient_responsibility=claim.patient_responsibility,
        claim_filing_indicator_code=claim.claim_filing_indicator_code,
        payer_claim_control_number=claim.payer_claim_control_number,
        patient=claim.patient,
        rendering_provider=claim.rendering_provider,
        dates=tuple(claim.dates),
        adjustments=tuple(claim.adjustments),
        mia=claim.mia,
        moa=claim.moa,
        remarks=tuple(claim.remarks),
        service_lines=tuple(claim.service_lines),
        total_allowed=total_allowed,
        total_paid_computed=total_paid_computed,
        is_reconciled=is_reconciled,
        reconciliation_diff=diff,
    )


def _finalize_transaction(txn: _TransactionBuilder) -> Transaction835:
    return Transaction835(
        control_number=txn.control_number,
        bpr_total_paid=txn.bpr_total_paid,
        bpr_payment_method=txn.bpr_payment_method,
        bpr_payment_date=txn.bpr_payment_date,
        trn_trace_number=txn.trn_trace_number,
        payer=txn.payer,
        payee=txn.payee,
        claims=tuple(txn.claims),
        plb_adjustments=tuple(txn.plb_adjustments),
    )


def parse_835(text: str) -> ParseResult:
    if not isinstance(text, str):
        raise TypeError(f"parse_835 requires a str, got {type(text).__name__}")

    if len(text) < ISA_MIN_LENGTH or not text.startswith("ISA"):
        return ParseResult(
            transactions=(),
            errors=(
                ParseIssue(
                    "ISA",
                    0,
                    "ISA segment missing or too short to determine delimiters",
                    "error",
                ),
            ),
        )

    element_sep = text[ISA_ELEMENT_SEP_INDEX]
    sub_element_sep = text[ISA_SUB_ELEMENT_SEP_INDEX]
    terminator = text[ISA_TERMINATOR_INDEX]

    body = text[ISA_MIN_LENGTH:]
    raw_segments = [chunk.strip("\r\n") for chunk in body.split(terminator)]
    raw_segments = [chunk for chunk in raw_segments if chunk]

    errors: list[ParseIssue] = []
    transactions: list[Transaction835] = []

    txn: _TransactionBuilder | None = None
    claim_ctx: _ClaimBuilder | None = None

    for index, raw in enumerate(raw_segments):
        elements = raw.split(element_sep)
        tag = elements[0] if elements else ""

        try:
            if tag == "ST":
                txn = _TransactionBuilder(control_number=_safe_element(elements, 2))
            elif tag == "GS" or tag == "GE" or tag == "IEA":
                pass
            elif tag == "BPR":
                if txn is None:
                    continue
                txn.bpr_total_paid = Money(elements[2])
                txn.bpr_payment_method = _safe_element(elements, 4)
                date_str = _safe_element(elements, 17)
                txn.bpr_payment_date = _parse_date(date_str) if date_str else None
            elif tag == "TRN":
                if txn is None:
                    continue
                txn.trn_trace_number = _safe_element(elements, 2) or None
            elif tag == "N1":
                if txn is None:
                    continue
                entity = Entity(
                    entity_identifier_code=elements[1],
                    name=_safe_element(elements, 2),
                    id_qualifier=_safe_element(elements, 3) or None,
                    id_code=_safe_element(elements, 4) or None,
                )
                if entity.entity_identifier_code == "PR":
                    txn.payer = entity
                elif entity.entity_identifier_code == "PE":
                    txn.payee = entity
            elif tag == "LX":
                pass
            elif tag == "CLP":
                if txn is None:
                    continue
                if claim_ctx is not None:
                    txn.claims.append(_finalize_claim(claim_ctx))
                    claim_ctx = None
                if len(elements) < 8:
                    errors.append(
                        ParseIssue(
                            "CLP",
                            index,
                            f"CLP: expected at least 7 elements, got {len(elements) - 1}",
                            "error",
                        )
                    )
                    continue
                status = _CLP_STATUS_BY_CODE.get(elements[2])
                if status is None:
                    errors.append(
                        ParseIssue("CLP", index, "CLP: unrecognized claim status code", "error")
                    )
                    continue
                claim_ctx = _ClaimBuilder(
                    patient_control_number=elements[1],
                    status=status,
                    total_charge=Money(elements[3]),
                    total_paid_reported=Money(elements[4]),
                    patient_responsibility=Money(elements[5]),
                    claim_filing_indicator_code=elements[6],
                    payer_claim_control_number=elements[7],
                )
            elif tag == "NM1":
                if claim_ctx is None:
                    continue
                entity = Entity(
                    entity_identifier_code=elements[1],
                    name=_safe_element(elements, 3),
                    id_qualifier=_safe_element(elements, 8) or None,
                    id_code=_safe_element(elements, 9) or None,
                )
                if entity.entity_identifier_code == "QC":
                    claim_ctx.patient = entity
                elif entity.entity_identifier_code == "82":
                    claim_ctx.rendering_provider = entity
            elif tag == "DTM":
                if claim_ctx is None:
                    continue
                qualifier = elements[1]
                value = _parse_date(elements[2])
                if claim_ctx.current_line is not None:
                    claim_ctx.current_line.service_date = value
                else:
                    claim_ctx.dates.append(DateRef(qualifier, value))
            elif tag == "CAS":
                if claim_ctx is None:
                    continue
                adjustments, cas_issues = _parse_cas(elements, index)
                errors.extend(cas_issues)
                if claim_ctx.current_line is not None:
                    claim_ctx.current_line.adjustments.extend(adjustments)
                else:
                    claim_ctx.adjustments.extend(adjustments)
            elif tag == "SVC":
                if claim_ctx is None:
                    continue
                if claim_ctx.current_line is not None:
                    claim_ctx.service_lines.append(_finalize_line(claim_ctx.current_line))
                composite = elements[1].split(sub_element_sep)
                procedure_code = composite[1] if len(composite) > 1 else composite[0]
                modifiers = tuple(composite[2:])
                charge = Money(elements[2])
                paid_reported = Money(_safe_element(elements, 3) or "0")
                # SVC04 -- NUBC revenue code. Was hardcoded None (B-16,
                # docs/audit/REGISTER.md): domain.contract._is_implant
                # matches on revenue code as well as procedure code, so
                # discarding this silently weakened implant detection on
                # the real ingestion path.
                revenue_code = _safe_element(elements, 4) or None
                units_str = _safe_element(elements, 5)
                units = Decimal(units_str) if units_str else Decimal(1)
                claim_ctx.current_line = _LineBuilder(
                    procedure_code=procedure_code,
                    modifiers=modifiers,
                    revenue_code=revenue_code,
                    charge=charge,
                    paid_reported=paid_reported,
                    units=units,
                    raw_elements=tuple(elements[1:]),
                )
            elif tag == "LQ":
                if claim_ctx is None:
                    continue
                code = _safe_element(elements, 2) or _safe_element(elements, 1)
                if claim_ctx.current_line is not None:
                    claim_ctx.current_line.remarks.append(code)
                else:
                    claim_ctx.remarks.append(code)
            elif tag == "MIA":
                if claim_ctx is None:
                    continue
                claim_ctx.mia = MIA(raw_elements=tuple(elements[1:]))
            elif tag == "MOA":
                if claim_ctx is None:
                    continue
                claim_ctx.moa = MOA(raw_elements=tuple(elements[1:]))
            elif tag == "PLB":
                if txn is None:
                    continue
                if claim_ctx is not None:
                    txn.claims.append(_finalize_claim(claim_ctx))
                    claim_ctx = None
                txn.plb_adjustments.extend(_parse_plb(elements))
            elif tag == "SE":
                if txn is None:
                    continue
                if claim_ctx is not None:
                    txn.claims.append(_finalize_claim(claim_ctx))
                    claim_ctx = None
                transactions.append(_finalize_transaction(txn))
                txn = None
            else:
                pass
        except (InvalidOperation, ValueError, IndexError) as exc:
            errors.append(
                ParseIssue(tag, index, f"{tag}: failed to parse ({type(exc).__name__})", "error")
            )
            if tag == "CLP":
                claim_ctx = None
            continue

    if txn is not None:
        if claim_ctx is not None:
            txn.claims.append(_finalize_claim(claim_ctx))
            claim_ctx = None
        transactions.append(_finalize_transaction(txn))
        errors.append(
            ParseIssue("SE", len(raw_segments), "transaction ended before SE trailer", "warning")
        )

    return ParseResult(tuple(transactions), tuple(errors))
