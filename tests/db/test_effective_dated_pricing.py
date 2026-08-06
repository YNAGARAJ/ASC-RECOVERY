"""A claim dated last year must price against last year's contract
version, not today's -- round-tripped through Postgres. This reuses the
already Phase-1-tested `domain.contract.find_effective_contract` /
`price_claim`; the only thing under test here is that
`repository.get_effective_contract_version` reconstructs the right domain
object from the database.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    ClaimLineInput,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
    price_claim,
)
from domain.money import Money, Rate
from tests.db.conftest import seed_org_facility_user

_CODE = "99213"


def _version(
    payer_id: str, effective_from: date, effective_to: date | None, rate: Money
) -> ContractVersion:
    return ContractVersion(
        payer_id=payer_id,
        effective_from=effective_from,
        effective_to=effective_to,
        default_pricing_method=PricingMethod.FEE_SCHEDULE,
        fee_schedule={_CODE: rate},
        percent_of_charge_rate=None,
        case_rate_groups=(),
        mppr_rule=MPPRRule(
            enabled=False,
            second_procedure_rate=Rate.percent(Decimal("50")),
            third_and_subsequent_rate=Rate.percent(Decimal("25")),
            exempt_codes=frozenset(),
        ),
        bilateral_rule=BilateralRule(
            enabled=False,
            total_rate=Rate.percent(Decimal("150")),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
        assistant_surgeon_rule=AssistantSurgeonRule(
            enabled=False, rate=Rate.percent(Decimal("16")), applicable_modifiers=frozenset()
        ),
        implant_carveout_rule=ImplantCarveoutRule(
            enabled=False, procedure_codes=frozenset(), revenue_codes=frozenset()
        ),
    )


def _seed_two_versions(
    owner_engine: Engine, session_factory: sessionmaker[Session]
) -> tuple[uuid.UUID, uuid.UUID, str]:
    payer_id = f"PAYER-{uuid.uuid4().hex[:8]}"
    old_version = _version(payer_id, date(2022, 1, 1), date(2022, 12, 31), Money("100.00"))
    new_version = _version(payer_id, date(2023, 1, 1), None, Money("120.00"))

    user_id, org_id, _facility_id = seed_org_facility_user(
        owner_engine, "Effective-dating test tenant"
    )

    with access_session(session_factory, user_id) as session:
        contract = repository.create_contract(session, org_id, payer_id, "Test Payer Contract")
        repository.create_contract_version(session, org_id, contract.id, old_version)
        repository.create_contract_version(session, org_id, contract.id, new_version)

    return user_id, org_id, payer_id


def test_claim_dated_last_year_prices_against_last_years_contract(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, org_id, payer_id = _seed_two_versions(owner_engine, app_session_factory)

    with access_session(app_session_factory, user_id) as session:
        effective = repository.get_effective_contract_version(
            session, org_id, payer_id, date(2022, 6, 15)
        )

    assert effective is not None
    assert effective.effective_from == date(2022, 1, 1)

    priced = price_claim(
        (ClaimLineInput(_CODE, (), None, Money("100.00"), None, Decimal("1")),), effective
    )
    assert priced.lines[0].allowed == Money("100.00")


def test_claim_dated_this_year_prices_against_current_contract(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, org_id, payer_id = _seed_two_versions(owner_engine, app_session_factory)

    with access_session(app_session_factory, user_id) as session:
        effective = repository.get_effective_contract_version(
            session, org_id, payer_id, date(2023, 6, 15)
        )

    assert effective is not None
    assert effective.effective_from == date(2023, 1, 1)

    priced = price_claim(
        (ClaimLineInput(_CODE, (), None, Money("120.00"), None, Decimal("1")),), effective
    )
    assert priced.lines[0].allowed == Money("120.00")


def test_claim_dated_before_any_contract_version_finds_nothing(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, org_id, payer_id = _seed_two_versions(owner_engine, app_session_factory)

    with access_session(app_session_factory, user_id) as session:
        effective = repository.get_effective_contract_version(
            session, org_id, payer_id, date(2020, 1, 1)
        )

    assert effective is None
