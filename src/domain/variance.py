"""Expected-vs-actual variance detection."""

from __future__ import annotations

import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from domain.contract import (
    ContractVersion,
    PricedClaim,
    PricedServiceLine,
    PricingMethodUsed,
    price_claim,
)
from domain.money import Money

_VARIANCE_TOLERANCE = Money("0.01")


class RootCause(enum.Enum):
    CORRECT_NO_VARIANCE = enum.auto()
    UNPRICED_CODE = enum.auto()
    DUPLICATE_LINE = enum.auto()
    MPPR_NOT_APPLIED = enum.auto()
    BILATERAL_MODIFIER_DROPPED = enum.auto()
    IMPLANT_NOT_CARVED_OUT = enum.auto()
    STOP_LOSS_NOT_APPLIED = enum.auto()
    STALE_FEE_SCHEDULE = enum.auto()
    UNDETERMINED_VARIANCE = enum.auto()


@dataclass(frozen=True, slots=True)
class ActualServiceLine:
    """Thin adapter over domain.x835.ServiceLine835 for variance comparison."""

    procedure_code: str
    service_date: date | None
    charge: Money
    allowed: Money


@dataclass(frozen=True, slots=True)
class Finding:
    claim_id: str
    line_index: int
    procedure_code: str
    expected_allowed: Money | None
    actual_allowed: Money
    shortfall: Money
    root_cause: RootCause
    evidence: str
    # Set only for a reversal-netting finding (ingestion.plan._reverse_finding)
    # -- carries the ORIGINAL finding's already-persisted service_line_id
    # forward, so persistence attaches the netting entry to the line it
    # actually nets rather than to whatever line happens to sit at the same
    # line_index on the reversal claim itself, which may have fewer lines or
    # a different procedure at that index. `line_index` above stays as the
    # original's index for evidence/reporting; this field is what persistence
    # actually keys off when it's set. See docs/audit/REGISTER.md F-01.
    service_line_id: uuid.UUID | None = None


def _within_tolerance(diff: Money) -> bool:
    return abs(diff) <= _VARIANCE_TOLERANCE


def _find_prior_version(
    current: ContractVersion, versions: Sequence[ContractVersion]
) -> ContractVersion | None:
    candidates = [
        v
        for v in versions
        if v.payer_id == current.payer_id and v.effective_from < current.effective_from
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.effective_from)


def _stale_fee_schedule_alternate_allowed(
    line: PricedServiceLine,
    current_version: ContractVersion,
    versions: Sequence[ContractVersion],
) -> Money | None:
    prior = _find_prior_version(current_version, versions)
    if prior is None:
        return None
    alternate = price_claim([line.raw_input], prior)
    return alternate.lines[0].allowed


def _evidence(
    procedure_code: str,
    expected_allowed: Money | None,
    actual_allowed: Money,
    shortfall: Money,
    root_cause: RootCause,
) -> str:
    expected_str = str(expected_allowed) if expected_allowed is not None else "UNPRICED"
    return (
        f"{procedure_code}: expected {expected_str}, actual {actual_allowed}, "
        f"shortfall {shortfall} [{root_cause.name}]"
    )


def evaluate_claim(
    claim_id: str,
    expected: PricedClaim,
    actual_lines: Sequence[ActualServiceLine],
    all_contract_versions: Sequence[ContractVersion] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    seen_actual_keys: set[tuple[str, date | None, Money]] = set()

    for i, expected_line in enumerate(expected.lines):
        actual = actual_lines[i]
        expected_allowed = expected_line.allowed
        actual_allowed = actual.allowed

        if expected_allowed is None:
            findings.append(
                Finding(
                    claim_id=claim_id,
                    line_index=i,
                    procedure_code=expected_line.procedure_code,
                    expected_allowed=None,
                    actual_allowed=actual_allowed,
                    shortfall=Money.zero(),
                    root_cause=RootCause.UNPRICED_CODE,
                    evidence=_evidence(
                        expected_line.procedure_code,
                        None,
                        actual_allowed,
                        Money.zero(),
                        RootCause.UNPRICED_CODE,
                    ),
                )
            )
            continue

        dedup_key = (actual.procedure_code, actual.service_date, actual.charge)
        shortfall = expected_allowed - actual_allowed

        if dedup_key in seen_actual_keys:
            root_cause = RootCause.DUPLICATE_LINE
        else:
            seen_actual_keys.add(dedup_key)
            if _within_tolerance(shortfall):
                root_cause = RootCause.CORRECT_NO_VARIANCE
            elif expected_line.pricing_method_used == PricingMethodUsed.MPPR_REDUCED:
                root_cause = RootCause.MPPR_NOT_APPLIED
            elif expected_line.pricing_method_used == PricingMethodUsed.BILATERAL:
                root_cause = RootCause.BILATERAL_MODIFIER_DROPPED
            elif expected_line.pricing_method_used == PricingMethodUsed.INVOICE_COST_IMPLANT:
                root_cause = RootCause.IMPLANT_NOT_CARVED_OUT
            elif expected_line.pricing_method_used == PricingMethodUsed.STOP_LOSS_OUTLIER:
                root_cause = RootCause.STOP_LOSS_NOT_APPLIED
            else:
                root_cause = RootCause.UNDETERMINED_VARIANCE
                if all_contract_versions is not None:
                    alternate_allowed = _stale_fee_schedule_alternate_allowed(
                        expected_line, expected.contract_version, all_contract_versions
                    )
                    if alternate_allowed is not None and _within_tolerance(
                        actual_allowed - alternate_allowed
                    ):
                        root_cause = RootCause.STALE_FEE_SCHEDULE

        findings.append(
            Finding(
                claim_id=claim_id,
                line_index=i,
                procedure_code=expected_line.procedure_code,
                expected_allowed=expected_allowed,
                actual_allowed=actual_allowed,
                shortfall=shortfall,
                root_cause=root_cause,
                evidence=_evidence(
                    expected_line.procedure_code,
                    expected_allowed,
                    actual_allowed,
                    shortfall,
                    root_cause,
                ),
            )
        )

    return tuple(findings)
