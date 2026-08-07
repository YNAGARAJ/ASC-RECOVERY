"""DB-backed proof of F-11's PostgresRepository-side alert wiring
(docs/audit/REGISTER.md): unusual PHI access volume. Lives inside
PostgresRepository itself (not the HTTP layer), so it's exercised
directly against the repository here rather than through a TestClient --
same style test_pipeline_observability_live_db.py already uses for the
tracer/instruments wiring.

The ingestion-failure-rate alert this file used to also cover here moved
to `src/jobs/runner.py::run_ingestion_job` in Phase 7 (`docs/MASTER-
BUILD-PROMPT-V2.md`) -- ingestion no longer runs inside
`PostgresRepository` at all, so that alert's wiring proof moved with it
to `tests/jobs/test_runner_live_db.py`.

Skips cleanly without `TEST_DATABASE_URL`, same pattern as every other
DB-backed test file in this repo. Written here, never executed in this
environment (no Docker/WSL/Postgres available) -- honest about being
unverified, same as the rest of this repo's DB-backed test files.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from api.repository import FindingFilters, Page, PostgresRepository
from db import repository as db_repository
from db.access import access_session
from db.base import make_engine, make_session_factory
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from observability.alerts import Alert
from packets.drafter import ScriptedPacketDrafter
from security.rbac import Role
from tests.db.conftest import seed_org_facility_user
from tests.domain.fixtures_x835 import minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")

_TEST_ENCRYPTOR = make_test_encryptor()


@dataclass
class _FakeNotificationPort:
    alerts: list[Alert] = field(default_factory=list)

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/api/test_alerts_live_db.py "
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


def _seed_tenant(
    owner_engine: Engine, session_factory: sessionmaker[Session], label: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (user_id, facility_id)."""
    user_id, org_id, facility_id = seed_org_facility_user(
        owner_engine, label, role=Role.BILLER
    )
    with access_session(session_factory, user_id) as session:
        contract = db_repository.create_contract(session, org_id, TEST_PAYER, f"{label} contract")
        db_repository.create_contract_version(
            session, org_id, contract.id, make_contract_version()
        )
    return user_id, facility_id


def _make_repository(
    session_factory: sessionmaker[Session], notifier: _FakeNotificationPort
) -> PostgresRepository:
    return PostgresRepository(
        session_factory,
        drafter=ScriptedPacketDrafter([]),
        encryptor=_TEST_ENCRYPTOR,
        notifier=notifier,
    )


def test_repeated_phi_access_fires_an_unusual_access_alert(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, facility_id = _seed_tenant(owner_engine, app_session_factory, "phi-access-alert")
    with access_session(app_session_factory, user_id) as session:
        outcome = ingest_file(
            session,
            facility_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="phi-access-alert-tester",
            scanner=EicarAwareScanner(),
            encryptor=_TEST_ENCRYPTOR,
        )
        assert outcome.status == "ingested"  # type: ignore[union-attr]

    notifier = _FakeNotificationPort()
    repository = _make_repository(app_session_factory, notifier)
    result = repository.list_findings(
        user_id, facility_id, filters=FindingFilters(), page=Page(limit=1)
    )
    finding_id = result.items[0].id

    for _ in range(50):  # evaluate_unusual_phi_access_alert's default threshold
        detail = repository.get_finding_detail(
            user_id, facility_id, finding_id, actor="repeated-viewer", role=Role.BILLER
        )
        assert detail is not None

    phi_alerts = [a for a in notifier.alerts if a.name == "unusual_phi_access_volume"]
    assert len(phi_alerts) == 1
