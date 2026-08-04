"""End-to-end proof against real Postgres + real RLS, for two
representative endpoints (list findings, finding detail). Confirms that
FakeRepository's tenant-isolation guarantee -- proven exhaustively in
test_authz_matrix.py without a database -- matches what actually happens
against a live one.

Skips cleanly without `TEST_DATABASE_URL`, same pattern as
tests/db/conftest.py and tests/ingestion/conftest.py. Written here, never
executed in this environment (no Docker/WSL/Postgres available) --
honest about being unverified, same as the rest of this repo's DB-backed
test files.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.repository import PostgresRepository
from db import repository as db_repository
from db.base import make_engine, make_session_factory
from db.tenancy import tenant_session
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from security.rbac import Role
from security.session import issue_session
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
JWT_SECRET = "test-only-secret-never-use-in-production"


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/api/test_endpoints_live_db.py "
            "needs a live Postgres 16 with migrations applied, connected as the "
            "asc_app role. See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


def _seed_tenant(session_factory: sessionmaker[Session], label: str) -> tuple[uuid.UUID, str]:
    """Returns (tenant_id, subject) -- subject is unique-constrained on the
    `users` table, so it's generated here (not caller-supplied) to survive
    repeated runs against a persistent test database."""
    subject = f"live-user-{label}-{uuid.uuid4().hex[:12]}"
    with session_factory() as session, session.begin():
        tenant = db_repository.create_tenant(session, f"Live tenant {label} {uuid.uuid4()}")
        db_repository.create_user(session, tenant.id, subject=subject, role=Role.BILLER.value)
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        contract = db_repository.create_contract(
            session, tenant_id, TEST_PAYER, f"{label} contract"
        )
        db_repository.create_contract_version(
            session, tenant_id, contract.id, make_contract_version()
        )
        outcome = ingest_file(
            session,
            tenant_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by=subject,
            scanner=EicarAwareScanner(),
        )
        assert outcome.status == "ingested"  # type: ignore[union-attr]

    return tenant_id, subject


@pytest.fixture
def live_client(app_session_factory: sessionmaker[Session]) -> TestClient:
    repository = PostgresRepository(app_session_factory)
    app = create_app(repository=repository, jwt_secret_key=JWT_SECRET)
    return TestClient(app)


def _auth_headers(subject: str) -> dict[str, str]:
    tokens = issue_session(JWT_SECRET, subject, Role.BILLER, mfa_verified=True)
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_findings_list_returns_only_own_tenant_row(
    live_client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    _, subject_a = _seed_tenant(app_session_factory, "list-a")
    _seed_tenant(app_session_factory, "list-b")

    response = live_client.get("/findings", headers=_auth_headers(subject_a))
    assert response.status_code == 200
    assert response.json()["page"]["total"] == 1


def test_finding_detail_cross_tenant_lookup_is_404_against_real_rls(
    live_client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    _, subject_a = _seed_tenant(app_session_factory, "detail-a")
    _, subject_b = _seed_tenant(app_session_factory, "detail-b")

    own_list = live_client.get("/findings", headers=_auth_headers(subject_a))
    other_list = live_client.get("/findings", headers=_auth_headers(subject_b))
    other_finding_id = other_list.json()["items"][0]["id"]

    cross_tenant_response = live_client.get(
        f"/findings/{other_finding_id}", headers=_auth_headers(subject_a)
    )

    assert own_list.json()["page"]["total"] == 1
    assert cross_tenant_response.status_code == 404
