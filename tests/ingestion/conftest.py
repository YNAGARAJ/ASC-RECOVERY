"""DB-backed fixtures for the apply/pipeline half of tests/ingestion/.

Same skip-without-a-live-Postgres pattern as tests/db/conftest.py --
duplicated rather than shared across sibling test packages, since pytest
conftest discovery is scoped to a directory and its descendants, not
siblings. See tests/db/conftest.py's docstring for why the skip check lives
in the session-scoped fixture itself rather than a separate autouse guard.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_engine, make_session_factory
from domain.money import Money
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from tests.db.conftest import seed_org_facility_user
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")


def make_test_encryptor() -> EnvelopeEncryptor:
    """A fresh EnvelopeEncryptor backed by an in-memory LocalKMS -- ingestion
    tests only need patient PHI to actually get encrypted before ingestion
    writes it; key persistence/rotation is tests/security/test_encryption.py's
    job, not this one."""
    kms = LocalKMS()
    kms.generate_kek("test-kek")
    return EnvelopeEncryptor(kms)


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/ingestion/'s apply/pipeline "
            "tests need a live Postgres 16 with migrations applied, connected "
            "as the asc_app role. See docs/DB_SETUP.md."
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


def seed_org_with_contract(
    owner_engine: Engine,
    label: str,
    *,
    fee_schedule: dict[str, Money] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """An org + facility + user + membership (via `seed_org_facility_user`,
    tests/db/conftest.py -- owner-role connection, RLS bootstrap) plus one
    open-ended fee-schedule contract for TEST_PAYER, covering 99213/99214
    -- enough for the fixtures in tests/domain/fixtures_x835.py to price
    against. `fee_schedule` defaults to make_contract_version()'s own
    default ($100 for 99213) -- override it when a test cares about the
    sign or size of the resulting shortfall (see
    test_pipeline_observability_live_db.py, which needs a genuine
    positive shortfall to prove the dollars_detected metric fires).
    Returns `(user_id, facility_id)` -- callers open their own
    `access_session(session_factory, user_id)` and pass `facility_id` to
    `ingest_file`."""
    user_id, org_id, facility_id = seed_org_facility_user(owner_engine, label)

    session_factory = make_session_factory(owner_engine)
    with access_session(session_factory, user_id) as session:
        contract = repository.create_contract(session, org_id, TEST_PAYER, "Test Contract")
        repository.create_contract_version(
            session,
            org_id,
            contract.id,
            make_contract_version(fee_schedule=fee_schedule),
        )

    return user_id, facility_id
