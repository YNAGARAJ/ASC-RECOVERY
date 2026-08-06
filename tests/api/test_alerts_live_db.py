"""DB-backed proof of F-11's PostgresRepository-side alert wiring
(docs/audit/REGISTER.md): unusual PHI access volume and ingestion failure
rate. Both live inside PostgresRepository itself (not the HTTP layer),
so they're exercised directly against the repository here rather than
through a TestClient -- same style test_pipeline_observability_live_db.py
already uses for the tracer/instruments wiring.

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
from sqlalchemy.orm import Session, sessionmaker

from api.repository import FindingFilters, Page, PostgresRepository
from db import repository as db_repository
from db.base import make_engine, make_session_factory
from db.tenancy import tenant_session
from ingestion.pipeline import ingest_file
from ingestion.virus_scan import EicarAwareScanner
from observability.alerts import Alert
from packets.drafter import ScriptedPacketDrafter
from tests.domain.fixtures_x835 import malformed_missing_isa, minimal_valid_835
from tests.ingestion.conftest import make_test_encryptor
from tests.ingestion.fixtures import TEST_PAYER, make_contract_version

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

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


def _seed_tenant(session_factory: sessionmaker[Session], label: str) -> uuid.UUID:
    with session_factory() as session, session.begin():
        tenant = db_repository.create_tenant(session, f"{label} {uuid.uuid4()}")
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        contract = db_repository.create_contract(
            session, tenant_id, TEST_PAYER, f"{label} contract"
        )
        db_repository.create_contract_version(
            session, tenant_id, contract.id, make_contract_version()
        )
    return tenant_id


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
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _seed_tenant(app_session_factory, "phi-access-alert")
    with tenant_session(app_session_factory, tenant_id) as session:
        outcome = ingest_file(
            session,
            tenant_id,
            content=minimal_valid_835().encode("utf-8"),
            source="upload",
            uploaded_by="phi-access-alert-tester",
            scanner=EicarAwareScanner(),
            encryptor=_TEST_ENCRYPTOR,
        )
        assert outcome.status == "ingested"  # type: ignore[union-attr]

    notifier = _FakeNotificationPort()
    repository = _make_repository(app_session_factory, notifier)
    result = repository.list_findings(tenant_id, filters=FindingFilters(), page=Page(limit=1))
    finding_id = result.items[0].id

    for _ in range(50):  # evaluate_unusual_phi_access_alert's default threshold
        detail = repository.get_finding_detail(tenant_id, finding_id, actor="repeated-viewer")
        assert detail is not None

    phi_alerts = [a for a in notifier.alerts if a.name == "unusual_phi_access_volume"]
    assert len(phi_alerts) == 1


def test_high_quarantine_rate_fires_an_ingestion_failure_alert(
    app_session_factory: sessionmaker[Session],
) -> None:
    tenant_id = _seed_tenant(app_session_factory, "ingestion-failure-alert")
    notifier = _FakeNotificationPort()
    repository = _make_repository(app_session_factory, notifier)

    # One clean ingestion, then two quarantined ones (distinct content each
    # time -- identical content would dedupe as DuplicateOutcome, which
    # this alert deliberately excludes, same as record_ingestion_outcome's
    # own metrics). 2/3 quarantined is well above the 10% default rate.
    good = repository.ingest_remittance(
        tenant_id,
        content=minimal_valid_835().encode("utf-8"),
        source="upload",
        uploaded_by="ingestion-failure-alert-tester",
        scanner=EicarAwareScanner(),
    )
    assert good.status == "ingested"  # type: ignore[union-attr]

    for i in range(2):
        content = f"{malformed_missing_isa()} seq={i}".encode()
        outcome = repository.ingest_remittance(
            tenant_id,
            content=content,
            source="upload",
            uploaded_by="ingestion-failure-alert-tester",
            scanner=EicarAwareScanner(),
        )
        assert outcome.status == "quarantined"  # type: ignore[union-attr]

    failure_alerts = [a for a in notifier.alerts if a.name == "ingestion_failure_rate"]
    assert len(failure_alerts) >= 1
