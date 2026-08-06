"""Onboard a new ASC customer: creates its organization, one facility, an
admin user, a membership binding them together, and (optionally) an
initial contract + fee-schedule version.

Deliberately a script, not an API endpoint. There is no "platform
superadmin" HTTP route that could call a `POST /organizations` endpoint
without breaking the resolved-access boundary this build has maintained
since Phase 4 -- everything the API layer does starts from an already-
authenticated `AuthContext`, which by definition doesn't exist yet for a
brand-new customer. This script uses the same direct-repository-call,
operator-run pattern as `scripts/db/init_roles.sql`: it connects with the
application's own `DATABASE_URL` (the `asc_app` role, same credentials
`src/main.py` uses) and calls straight into `db.repository`, bypassing
HTTP entirely.

Anything beyond the initial contract version (additional payers, later fee
schedule updates, additional facilities/memberships) goes through the
existing API once the customer's first admin user can authenticate --
this script's job ends at making that first login possible.

Usage:
    DATABASE_URL=postgresql+psycopg://asc_app:...@host/db \\
        python scripts/onboard_customer.py path/to/config.json

See docs/RUNBOOK.md for the config file shape and a worked example.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from db import repository as db_repository
from db.access import access_session
from db.base import make_engine, make_session_factory
from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
)
from domain.money import Money, Rate
from security.rbac import Role


class ConfigError(ValueError):
    pass


def _require_key(config: dict[str, Any], key: str) -> Any:
    if key not in config:
        raise ConfigError(f"onboarding config is missing required key {key!r}")
    return config[key]


def _build_contract_version(payer_id: str, contract_cfg: dict[str, Any]) -> ContractVersion:
    """Fee schedule + effective dates only -- payment rules (MPPR, bilateral,
    assistant surgeon, implant carve-out) start disabled and are configured
    later via `POST /contracts/{id}/versions`, the same as any subsequent
    version. Keeping this script's input surface small avoids duplicating
    that endpoint's full rule schema for a one-time bootstrap step."""
    fee_schedule = {
        code: Money(amount)
        for code, amount in _require_key(contract_cfg, "fee_schedule").items()
    }
    effective_to_raw = contract_cfg.get("effective_to")
    percent_raw = contract_cfg.get("percent_of_charge_rate_percent")
    return ContractVersion(
        payer_id=payer_id,
        effective_from=date.fromisoformat(_require_key(contract_cfg, "effective_from")),
        effective_to=date.fromisoformat(effective_to_raw) if effective_to_raw else None,
        default_pricing_method=PricingMethod(
            contract_cfg.get("default_pricing_method", "fee_schedule")
        ),
        fee_schedule=fee_schedule,
        percent_of_charge_rate=Rate.percent(percent_raw) if percent_raw is not None else None,
        case_rate_groups=(),
        mppr_rule=MPPRRule(
            enabled=False,
            second_procedure_rate=Rate.percent("50"),
            third_and_subsequent_rate=Rate.percent("25"),
            exempt_codes=frozenset(),
        ),
        bilateral_rule=BilateralRule(
            enabled=False,
            total_rate=Rate.percent("150"),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
        assistant_surgeon_rule=AssistantSurgeonRule(
            enabled=False, rate=Rate.percent("16"), applicable_modifiers=frozenset()
        ),
        implant_carveout_rule=ImplantCarveoutRule(
            enabled=False, procedure_codes=frozenset(), revenue_codes=frozenset()
        ),
    )


_VALID_ORG_TYPES = frozenset({"PLATFORM", "BILLING_COMPANY", "ASC_GROUP", "ASC"})
_VALID_SCOPES = frozenset({"ALL_FACILITIES", "SPECIFIC_FACILITIES"})


def onboard(database_url: str, config: dict[str, Any]) -> None:
    org_name = _require_key(config, "org_name")
    org_type = config.get("org_type", "ASC")
    if org_type not in _VALID_ORG_TYPES:
        raise ConfigError(f"org_type must be one of {sorted(_VALID_ORG_TYPES)}, got {org_type!r}")
    facility_name = config.get("facility_name", org_name)
    admin_subject = _require_key(config, "admin_subject")
    admin_role = config.get("admin_role", Role.ORG_ADMIN.value)
    valid_roles = {role.value for role in Role}
    if admin_role not in valid_roles:
        raise ConfigError(f"admin_role must be one of {sorted(valid_roles)}, got {admin_role!r}")
    membership_scope = config.get("membership_scope", "ALL_FACILITIES")
    if membership_scope not in _VALID_SCOPES:
        raise ConfigError(
            f"membership_scope must be one of {sorted(_VALID_SCOPES)}, got {membership_scope!r}"
        )

    session_factory = make_session_factory(make_engine(database_url))

    # Deliberately a single plain session, not access_session -- every
    # table touched here (organizations, facilities, users, memberships)
    # is RLS-protected against *resolved* access, and there is no
    # membership yet for anyone to resolve until this transaction commits
    # (the same bootstrap problem db.models.User's docstring describes,
    # now spanning more tables). This script must run with asc_owner-
    # level credentials (BYPASSRLS -- see
    # alembic/versions/0001_initial_schema.py's own docstring for why
    # asc_owner needs that attribute), never asc_app's constrained
    # runtime role, exactly like running migrations does.
    with session_factory() as session, session.begin():
        org = db_repository.create_organization(
            session, parent_org_id=None, type=org_type, name=org_name
        )
        facility = db_repository.create_facility(session, org.id, name=facility_name)
        user = db_repository.create_user(session, subject=admin_subject)
        membership = db_repository.create_membership(
            session, user.id, org.id, role=admin_role, scope=membership_scope
        )
    print(
        f"Created organization {org.id} ({org_name!r}, type={org_type!r}) with facility "
        f"{facility.id} ({facility_name!r}), admin user {user.id} (subject={admin_subject!r}), "
        f"and membership {membership.id} (role={admin_role!r}, scope={membership_scope!r})"
    )

    contract_cfg = config.get("contract")
    if contract_cfg is None:
        return

    payer_id = _require_key(contract_cfg, "payer_id")
    contract_name = _require_key(contract_cfg, "name")
    version = _build_contract_version(payer_id, contract_cfg)
    with access_session(session_factory, user.id) as session:
        contract = db_repository.create_contract(session, org.id, payer_id, contract_name)
        contract_version = db_repository.create_contract_version(
            session, org.id, contract.id, version
        )
    print(
        f"Created contract {contract.id} ({contract_name!r}, payer {payer_id!r}) "
        f"with initial version {contract_version.id}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, help="Path to a JSON onboarding config")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    config = json.loads(args.config.read_text())
    try:
        onboard(database_url, config)
    except ConfigError as exc:
        print(f"Invalid onboarding config: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
