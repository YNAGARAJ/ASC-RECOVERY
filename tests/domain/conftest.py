"""Shared synthetic fixtures for domain tests.

All patient/provider identifiers here are deliberately synthetic and shaped so
they can never match a real SSN or Medicare Beneficiary Identifier pattern
(see scripts/hooks/block_phi.sh) -- e.g. "TESTMBR000001", never NNN-NN-NNNN or
an MBI-shaped string. No real PHI, per CLAUDE.md rule 1.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    CaseRateGroup,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
    StopLossRule,
)
from domain.money import Money, Rate

SAFE_PATIENT_NAME = "PATIENT ONE"
SAFE_PATIENT_FIRST_NAME = "TESTFIRST"
SAFE_MEMBER_ID = "TESTMBR000001"
SAFE_PROVIDER_NAME = "TEST RENDERING PROVIDER"
SAFE_PROVIDER_NPI = "1999999999"
SAFE_PAYER_NAME = "TEST PAYER"
SAFE_PAYEE_NAME = "TEST ASC"


def _default_fee_schedule() -> dict[str, Money]:
    # Built lazily (not as a module-level constant) so importing this module
    # -- which happens at test-collection time, before Money is implemented
    # -- doesn't itself raise NotImplementedError.
    return {
        "99213": Money("100.00"),
        "99214": Money("150.00"),
        "27447": Money("5000.00"),
        "27446": Money("4000.00"),
    }


IMPLANT_CODE = "L8699"
CASE_RATE_TRIGGER_CODE = "CASERATE1"

ContractFactory = Callable[..., ContractVersion]


@pytest.fixture
def make_contract_version() -> ContractFactory:
    """Factory fixture: builds a baseline ContractVersion, overridable per test.

    All rules are enabled with representative rates by default so a test only
    needs to override the one rule it's exercising.
    """

    def _make(
        *,
        payer_id: str = "PAYER1",
        effective_from: date = date(2023, 1, 1),
        effective_to: date | None = None,
        default_pricing_method: PricingMethod = PricingMethod.FEE_SCHEDULE,
        fee_schedule: dict[str, Money] | None = None,
        percent_of_charge_rate: Rate | None = None,
        case_rate_groups: tuple[CaseRateGroup, ...] = (),
        mppr_rule: MPPRRule | None = None,
        bilateral_rule: BilateralRule | None = None,
        assistant_surgeon_rule: AssistantSurgeonRule | None = None,
        implant_carveout_rule: ImplantCarveoutRule | None = None,
        lesser_of_charge_enabled: bool = False,
        stop_loss_rule: StopLossRule | None = None,
    ) -> ContractVersion:
        return ContractVersion(
            payer_id=payer_id,
            effective_from=effective_from,
            effective_to=effective_to,
            default_pricing_method=default_pricing_method,
            fee_schedule=fee_schedule if fee_schedule is not None else _default_fee_schedule(),
            percent_of_charge_rate=percent_of_charge_rate,
            case_rate_groups=case_rate_groups,
            mppr_rule=mppr_rule
            if mppr_rule is not None
            else MPPRRule(
                enabled=True,
                second_procedure_rate=Rate.percent(Decimal("50")),
                third_and_subsequent_rate=Rate.percent(Decimal("25")),
                exempt_codes=frozenset(),
            ),
            bilateral_rule=bilateral_rule
            if bilateral_rule is not None
            else BilateralRule(
                enabled=True,
                total_rate=Rate.percent(Decimal("150")),
                convention=BilateralConvention.SINGLE_LINE_150_PCT,
            ),
            assistant_surgeon_rule=assistant_surgeon_rule
            if assistant_surgeon_rule is not None
            else AssistantSurgeonRule(
                enabled=True,
                rate=Rate.percent(Decimal("16")),
                applicable_modifiers=frozenset({"80", "81", "82", "AS"}),
            ),
            implant_carveout_rule=implant_carveout_rule
            if implant_carveout_rule is not None
            else ImplantCarveoutRule(
                enabled=True,
                procedure_codes=frozenset({IMPLANT_CODE}),
                revenue_codes=frozenset({"0278"}),
            ),
            lesser_of_charge_enabled=lesser_of_charge_enabled,
            stop_loss_rule=stop_loss_rule
            if stop_loss_rule is not None
            else StopLossRule(
                enabled=False,
                threshold=Money.zero(),
                outlier_rate=Rate.percent(Decimal("0")),
                first_dollar=True,
            ),
        )

    return _make
