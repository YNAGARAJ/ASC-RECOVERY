"""Fee schedule and payment rule pricing."""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from domain.money import Money, Rate


class PricingMethod(enum.Enum):
    FEE_SCHEDULE = "fee_schedule"
    PERCENT_OF_CHARGE = "percent_of_charge"


class BilateralConvention(enum.Enum):
    SINGLE_LINE_150_PCT = "single_line_150_pct"
    TWO_LINE_SPLIT = "two_line_split"


class PricingMethodUsed(enum.Enum):
    FEE_SCHEDULE = enum.auto()
    PERCENT_OF_CHARGE = enum.auto()
    INVOICE_COST_IMPLANT = enum.auto()
    BILATERAL = enum.auto()
    ASSISTANT_SURGEON = enum.auto()
    CASE_RATE_ALLOCATED = enum.auto()
    MPPR_REDUCED = enum.auto()
    UNPRICED = enum.auto()


@dataclass(frozen=True, slots=True)
class MPPRRule:
    enabled: bool
    second_procedure_rate: Rate
    third_and_subsequent_rate: Rate
    exempt_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class BilateralRule:
    enabled: bool
    total_rate: Rate
    convention: BilateralConvention


@dataclass(frozen=True, slots=True)
class AssistantSurgeonRule:
    enabled: bool
    rate: Rate
    applicable_modifiers: frozenset[str]


@dataclass(frozen=True, slots=True)
class ImplantCarveoutRule:
    enabled: bool
    procedure_codes: frozenset[str]
    revenue_codes: frozenset[str]


@dataclass(frozen=True, slots=True)
class CaseRateGroup:
    trigger_procedure_codes: frozenset[str]
    flat_rate: Money
    includes_implants: bool


@dataclass(frozen=True, slots=True)
class ContractVersion:
    payer_id: str
    effective_from: date
    effective_to: date | None
    default_pricing_method: PricingMethod
    fee_schedule: Mapping[str, Money]
    percent_of_charge_rate: Rate | None
    case_rate_groups: tuple[CaseRateGroup, ...]
    mppr_rule: MPPRRule
    bilateral_rule: BilateralRule
    assistant_surgeon_rule: AssistantSurgeonRule
    implant_carveout_rule: ImplantCarveoutRule


@dataclass(frozen=True, slots=True)
class ClaimLineInput:
    procedure_code: str
    modifiers: tuple[str, ...]
    revenue_code: str | None
    charge: Money
    invoice_cost: Money | None
    units: Decimal


@dataclass(frozen=True, slots=True)
class PricedServiceLine:
    procedure_code: str
    allowed: Money | None
    pricing_method_used: PricingMethodUsed
    mppr_rank: int | None
    raw_input: ClaimLineInput


@dataclass(frozen=True, slots=True)
class PricedClaim:
    lines: tuple[PricedServiceLine, ...]
    total_allowed: Money
    contract_version: ContractVersion


def find_effective_contract(
    payer_id: str, date_of_service: date, versions: Sequence[ContractVersion]
) -> ContractVersion | None:
    for version in versions:
        if version.payer_id != payer_id:
            continue
        if version.effective_from > date_of_service:
            continue
        if version.effective_to is not None and date_of_service > version.effective_to:
            continue
        return version
    return None


def _base_price(
    line: ClaimLineInput, contract: ContractVersion
) -> tuple[Money | None, PricingMethodUsed]:
    fee_schedule_amount = contract.fee_schedule.get(line.procedure_code)
    if contract.default_pricing_method == PricingMethod.FEE_SCHEDULE:
        if fee_schedule_amount is not None:
            return fee_schedule_amount, PricingMethodUsed.FEE_SCHEDULE
        if contract.percent_of_charge_rate is not None:
            return line.charge.times(
                contract.percent_of_charge_rate
            ), PricingMethodUsed.PERCENT_OF_CHARGE
        return None, PricingMethodUsed.UNPRICED
    if contract.percent_of_charge_rate is not None:
        return line.charge.times(
            contract.percent_of_charge_rate
        ), PricingMethodUsed.PERCENT_OF_CHARGE
    if fee_schedule_amount is not None:
        return fee_schedule_amount, PricingMethodUsed.FEE_SCHEDULE
    return None, PricingMethodUsed.UNPRICED


def _is_implant(line: ClaimLineInput, rule: ImplantCarveoutRule) -> bool:
    if not rule.enabled:
        return False
    if line.procedure_code in rule.procedure_codes:
        return True
    return line.revenue_code is not None and line.revenue_code in rule.revenue_codes


def price_claim(lines: Sequence[ClaimLineInput], contract: ContractVersion) -> PricedClaim:
    line_list = list(lines)
    n = len(line_list)

    allowed: list[Money | None] = [None] * n
    method: list[PricingMethodUsed] = [PricingMethodUsed.UNPRICED] * n
    excluded_from_mppr: list[bool] = [False] * n
    is_implant_line: list[bool] = [False] * n
    mppr_rank: list[int | None] = [None] * n

    # 1. Base price: fee schedule or percent-of-charge.
    for i, line in enumerate(line_list):
        allowed[i], method[i] = _base_price(line, contract)

    # 2. Implant carve-out: 100% of caller-supplied invoice cost, never fee schedule.
    for i, line in enumerate(line_list):
        if not _is_implant(line, contract.implant_carveout_rule):
            continue
        is_implant_line[i] = True
        excluded_from_mppr[i] = True
        if line.invoice_cost is not None:
            allowed[i] = line.invoice_cost
            method[i] = PricingMethodUsed.INVOICE_COST_IMPLANT
        else:
            allowed[i] = None
            method[i] = PricingMethodUsed.UNPRICED

    # 3. Bilateral modifier 50: single line at total_rate (150% by convention/default).
    bilateral_rule = contract.bilateral_rule
    if (
        bilateral_rule.enabled
        and bilateral_rule.convention == BilateralConvention.SINGLE_LINE_150_PCT
    ):
        for i, line in enumerate(line_list):
            if is_implant_line[i] or allowed[i] is None:
                continue
            if "50" not in line.modifiers:
                continue
            allowed[i] = allowed[i].times(bilateral_rule.total_rate)  # type: ignore[union-attr]
            method[i] = PricingMethodUsed.BILATERAL

    # 4. Assistant surgeon: priced off the primary line's already-computed allowed.
    assistant_rule = contract.assistant_surgeon_rule
    if assistant_rule.enabled:
        for i, line in enumerate(line_list):
            if is_implant_line[i]:
                continue
            if not (set(line.modifiers) & assistant_rule.applicable_modifiers):
                continue
            primary_index = next(
                (
                    j
                    for j, other in enumerate(line_list)
                    if j != i
                    and other.procedure_code == line.procedure_code
                    and not (set(other.modifiers) & assistant_rule.applicable_modifiers)
                ),
                None,
            )
            if primary_index is None or allowed[primary_index] is None:
                continue
            allowed[i] = allowed[primary_index].times(assistant_rule.rate)  # type: ignore[union-attr]
            method[i] = PricingMethodUsed.ASSISTANT_SURGEON
            excluded_from_mppr[i] = True

    # 5. Case rate: flat claim-level bundle, allocated back across eligible lines.
    for group in contract.case_rate_groups:
        triggered = any(line.procedure_code in group.trigger_procedure_codes for line in line_list)
        if not triggered:
            continue
        eligible_indices = [
            i for i in range(n) if not (is_implant_line[i] and not group.includes_implants)
        ]
        if eligible_indices:
            shares = group.flat_rate.allocate(len(eligible_indices))
            for share, i in zip(shares, eligible_indices, strict=True):
                allowed[i] = share
                method[i] = PricingMethodUsed.CASE_RATE_ALLOCATED
                excluded_from_mppr[i] = True
        break

    # 6. MPPR ranking: highest current allowed first, among the remaining pool.
    mppr_rule = contract.mppr_rule
    if mppr_rule.enabled:
        pool = [
            i
            for i in range(n)
            if not excluded_from_mppr[i]
            and allowed[i] is not None
            and line_list[i].procedure_code not in mppr_rule.exempt_codes
        ]
        pool.sort(key=lambda i: allowed[i], reverse=True)  # type: ignore[arg-type, return-value]
        for rank, i in enumerate(pool, start=1):
            mppr_rank[i] = rank
            if rank == 2:
                allowed[i] = allowed[i].times(mppr_rule.second_procedure_rate)  # type: ignore[union-attr]
                method[i] = PricingMethodUsed.MPPR_REDUCED
            elif rank >= 3:
                allowed[i] = allowed[i].times(mppr_rule.third_and_subsequent_rate)  # type: ignore[union-attr]
                method[i] = PricingMethodUsed.MPPR_REDUCED

    priced_lines = tuple(
        PricedServiceLine(
            procedure_code=line_list[i].procedure_code,
            allowed=allowed[i],
            pricing_method_used=method[i],
            mppr_rank=mppr_rank[i],
            raw_input=line_list[i],
        )
        for i in range(n)
    )
    total_allowed = sum((p.allowed for p in priced_lines if p.allowed is not None), Money.zero())
    return PricedClaim(lines=priced_lines, total_allowed=total_allowed, contract_version=contract)
