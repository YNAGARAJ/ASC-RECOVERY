"""End-to-end proof of `POST /contracts/{id}/versions` against real
Postgres -- no dedicated test of this route existed before Phase 8
(`docs/MASTER-BUILD-PROMPT-V2.md`); every prior contract-version fixture
seeded rows directly via `db.repository.create_contract_version`,
bypassing the HTTP layer entirely. Closes that gap while proving Phase
8's own additions (`lesser_of_charge_enabled`, `stop_loss_rule`) and the
F-16 audit-amendment fix (`BilateralRuleIn.convention`, previously
missing -- `BilateralConvention.TWO_LINE_SPLIT` was unreachable via the
only production entry point even though its domain-layer pricing branch
was already fixed) actually round-trip through the real route, not just
the repository layer underneath it.

Skips cleanly without `TEST_DATABASE_URL`, same pattern as every other
DB-backed test file in this repo. Written here, never executed in this
environment (no Docker/WSL/Postgres available) -- honest about being
unverified, same as the rest of this repo's DB-backed test files.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.repository import PostgresRepository
from db import repository as db_repository
from db.base import make_engine, make_session_factory
from db.models import User as UserModel
from domain.contract import BilateralConvention, ClaimLineInput, PricingMethodUsed, price_claim
from domain.money import Money
from packets.drafter import ScriptedPacketDrafter
from security.rbac import Role
from security.session import issue_session
from tests.db.conftest import seed_org_facility_user
from tests.ingestion.conftest import make_test_encryptor

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET = "test-only-secret-never-use-in-production"
_TEST_ENCRYPTOR = make_test_encryptor()


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/api/test_contracts_live_db.py "
            "needs a live Postgres 16 with migrations applied, connected as the "
            "asc_app role. See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    _require_database_url()
    url = OWNER_DATABASE_URL or TEST_DATABASE_URL
    assert url is not None
    return make_engine(url)


def _auth_headers(subject: str, org_id: uuid.UUID) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, str(org_id), mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_contract_version_created_via_api_persists_lesser_of_stop_loss_and_bilateral_convention(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    # manage_contract is platform_admin/org_admin only (docs/PERMISSIONS.md).
    user_id, org_id, _facility_id = seed_org_facility_user(
        owner_engine, "Contract API tenant", role=Role.ORG_ADMIN
    )
    with app_session_factory() as session:
        row = session.get(UserModel, user_id)
        assert row is not None
        subject = row.subject

    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)
    headers = _auth_headers(subject, org_id)

    created = client.post(
        "/contracts", headers=headers, json={"payer_id": "API-TEST-PAYER", "name": "API contract"}
    )
    assert created.status_code == 201
    contract_id = created.json()["id"]

    version = client.post(
        f"/contracts/{contract_id}/versions",
        headers=headers,
        json={
            "effective_from": str(date(2023, 1, 1)),
            "fee_schedule": {"27447": "1000.00"},
            "lesser_of_charge_enabled": True,
            "stop_loss_rule": {
                "enabled": True,
                "threshold": "10000.00",
                "outlier_rate_percent": "35",
                "first_dollar": True,
            },
            "bilateral_rule": {
                "enabled": True,
                "total_rate_percent": "150",
                "convention": "two_line_split",
            },
        },
    )
    assert version.status_code == 201
    version_id = version.json()["id"]

    with app_session_factory() as session:
        effective = db_repository.get_effective_contract_version(
            session, org_id, "API-TEST-PAYER", date(2023, 6, 1)
        )
    assert effective is not None
    assert effective.lesser_of_charge_enabled is True
    assert effective.stop_loss_rule.enabled is True
    assert effective.stop_loss_rule.threshold == Money("10000.00")
    # F-16 audit-amendment fix, closed for real: a convention selected via
    # the API now actually reaches the domain object -- previously
    # api/repository.py hardcoded SINGLE_LINE_150_PCT unconditionally,
    # making TWO_LINE_SPLIT unreachable in production despite its
    # domain-layer pricing branch already being fixed.
    assert effective.bilateral_rule.convention == BilateralConvention.TWO_LINE_SPLIT

    # Prove it's not just a stored value -- a real two-line bilateral
    # claim against this API-created contract actually prices with the
    # 100%/remainder split, not a flat 150% on each line.
    lines = (
        ClaimLineInput("27447", ("50",), None, Money("1000.00"), None, Decimal("1")),
        ClaimLineInput("27447", ("50",), None, Money("1000.00"), None, Decimal("1")),
    )
    priced = price_claim(lines, effective)
    allowed_amounts = sorted(
        (p.allowed for p in priced.lines if p.allowed is not None), reverse=True
    )
    assert allowed_amounts == [Money("1000.00"), Money("500.00")]
    assert all(p.pricing_method_used == PricingMethodUsed.BILATERAL for p in priced.lines)

    # And the version id the route returned really is the row we just read.
    with app_session_factory() as session:
        rows = db_repository.list_contract_versions(session, org_id, "API-TEST-PAYER")
    assert any(str(row_id) == version_id for row_id, _v in rows)


def test_contract_version_defaults_lesser_of_charge_enabled_true_when_omitted(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    """The API-level default matches the migration's own column default
    (alembic/versions/0013_contract_stop_loss_lesser_of.py) -- a caller
    that doesn't mention the field at all still gets the false-positive-
    safe behavior, not an implicit False."""
    user_id, org_id, _facility_id = seed_org_facility_user(
        owner_engine, "Contract API defaults tenant", role=Role.ORG_ADMIN
    )
    with app_session_factory() as session:
        row = session.get(UserModel, user_id)
        assert row is not None
        subject = row.subject

    repository = PostgresRepository(
        app_session_factory, drafter=ScriptedPacketDrafter([]), encryptor=_TEST_ENCRYPTOR
    )
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    client = TestClient(app)
    headers = _auth_headers(subject, org_id)

    created = client.post(
        "/contracts", headers=headers, json={"payer_id": "API-DEFAULT-PAYER", "name": "Default"}
    )
    assert created.status_code == 201
    contract_id = created.json()["id"]

    version = client.post(
        f"/contracts/{contract_id}/versions",
        headers=headers,
        json={"effective_from": str(date(2023, 1, 1)), "fee_schedule": {"99213": "100.00"}},
    )
    assert version.status_code == 201

    with app_session_factory() as session:
        effective = db_repository.get_effective_contract_version(
            session, org_id, "API-DEFAULT-PAYER", date(2023, 6, 1)
        )
    assert effective is not None
    assert effective.lesser_of_charge_enabled is True
    assert effective.stop_loss_rule.enabled is False
