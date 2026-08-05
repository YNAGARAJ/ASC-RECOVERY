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
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.base import make_engine, make_session_factory
from db.tenancy import tenant_session
from security.encryption import EnvelopeEncryptor
from security.kms_local import LocalKMS
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


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


def seed_tenant_with_contract(
    session_factory: sessionmaker[Session], label: str
) -> uuid.UUID:
    """A tenant plus one open-ended fee-schedule contract for TEST_PAYER,
    covering 99213/99214 -- enough for the fixtures in
    tests/domain/fixtures_x835.py to price against.

    `tenants` has no RLS (ungated, like `users`), so creating it needs no
    tenant context -- but `contracts` does, so creating *that* outside
    `tenant_session` fails against a real Postgres with "unrecognized
    configuration parameter app.tenant_id" the moment its RLS policy's
    WITH CHECK clause tries to read a setting nothing ever SET. Matches
    the pattern tests/api/test_endpoints_live_db.py's `_seed_tenant`
    already uses correctly."""
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"{label} {uuid.uuid4()}")
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        contract = repository.create_contract(session, tenant_id, TEST_PAYER, "Test Contract")
        repository.create_contract_version(
            session, tenant_id, contract.id, make_contract_version()
        )

    return tenant_id
