"""Onboard a new ASC tenant: creates the tenant, its first admin user, and
(optionally) an initial contract + fee-schedule version.

Deliberately a script, not an API endpoint. `security/rbac.py` is entirely
tenant-scoped -- there is no "platform superadmin" role that could call a
`POST /tenants` endpoint without breaking the no-cross-tenant-access
boundary this build has maintained since Phase 3. This script uses the same
direct-repository-call, operator-run pattern as `scripts/db/init_roles.sql`:
it connects with the application's own `DATABASE_URL` (the `asc_app` role,
same credentials `src/main.py` uses) and calls straight into
`db.repository`, bypassing HTTP entirely.

Anything beyond the initial contract version (additional payers, later fee
schedule updates) goes through the existing `POST /contracts` and
`POST /contracts/{id}/versions` API once the tenant's first admin user can
authenticate -- this script's job ends at making that first login possible.

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
from db.base import make_engine, make_session_factory
from db.tenancy import tenant_session
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


def onboard(database_url: str, config: dict[str, Any]) -> None:
    tenant_name = _require_key(config, "tenant_name")
    admin_subject = _require_key(config, "admin_subject")
    admin_role = config.get("admin_role", Role.ADMIN.value)
    valid_roles = {role.value for role in Role}
    if admin_role not in valid_roles:
        raise ConfigError(f"admin_role must be one of {sorted(valid_roles)}, got {admin_role!r}")

    session_factory = make_session_factory(make_engine(database_url))

    # Tenant/User creation is deliberately outside tenant_session -- these
    # two tables are ungated (db.models.User's docstring), and there is no
    # tenant_id yet to scope to until create_tenant returns one.
    with session_factory() as session, session.begin():
        tenant = db_repository.create_tenant(session, tenant_name)
        user = db_repository.create_user(
            session, tenant.id, subject=admin_subject, role=admin_role
        )
    print(
        f"Created tenant {tenant.id} ({tenant_name!r}) with admin user {user.id} "
        f"(subject={admin_subject!r}, role={admin_role!r})"
    )

    contract_cfg = config.get("contract")
    if contract_cfg is None:
        return

    payer_id = _require_key(contract_cfg, "payer_id")
    contract_name = _require_key(contract_cfg, "name")
    version = _build_contract_version(payer_id, contract_cfg)
    with tenant_session(session_factory, tenant.id) as session:
        contract = db_repository.create_contract(session, tenant.id, payer_id, contract_name)
        contract_version = db_repository.create_contract_version(
            session, tenant.id, contract.id, version
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
