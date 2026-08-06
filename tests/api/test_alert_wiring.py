"""Tests for F-11's route-level alert wiring (docs/audit/REGISTER.md):
auth-anomaly (login failures) and cross-tenant-probe (404 bursts on
direct-id lookups), both dispatched through app.state.notifier. The
PHI-access-volume and ingestion-failure alerts live inside
PostgresRepository instead (DB-backed only, see
tests/api/test_endpoints_live_db.py) -- FakeRepository doesn't perform
the real DB writes that trigger them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import create_app
from observability.alerts import Alert
from security.mfa import generate_enrollment_secret
from security.rbac import Role
from tests.api.conftest import JWT_SECRET, SeedIds, auth_headers
from tests.api.fakes import FakeRepository


@dataclass
class FakeNotificationPort:
    alerts: list[Alert] = field(default_factory=list)

    def notify(self, alert: Alert) -> None:
        self.alerts.append(alert)


@pytest.fixture
def notifier() -> FakeNotificationPort:
    return FakeNotificationPort()


@pytest.fixture
def alert_client(repo: FakeRepository, notifier: FakeNotificationPort) -> TestClient:
    app: FastAPI = create_app(repository=repo, jwt_secret_key=JWT_SECRET, notifier=notifier)
    return TestClient(app)


# --- auth anomaly -----------------------------------------------------------


def test_repeated_login_failures_fire_an_auth_anomaly_alert(
    alert_client: TestClient, repo: FakeRepository, notifier: FakeNotificationPort
) -> None:
    subject = "biller-anomaly@example.com"
    mfa_secret = generate_enrollment_secret()
    repo.seed_login_credentials(
        subject,
        role=Role.BILLER.value,
        password="correct horse battery staple",
        mfa_secret=mfa_secret,
    )
    wrong = {
        "subject": subject,
        "password": "wrong",
        "totp_code": pyotp.TOTP(mfa_secret).now(),
    }

    for _ in range(5):  # evaluate_auth_anomaly_alert's default threshold
        alert_client.post("/auth/login", json=wrong)

    auth_alerts = [a for a in notifier.alerts if a.name == "auth_anomaly"]
    assert len(auth_alerts) == 1
    assert subject in auth_alerts[0].message


def test_a_single_login_failure_does_not_fire_the_alert(
    alert_client: TestClient, repo: FakeRepository, notifier: FakeNotificationPort
) -> None:
    subject = "biller-single-fail@example.com"
    mfa_secret = generate_enrollment_secret()
    repo.seed_login_credentials(
        subject,
        role=Role.BILLER.value,
        password="correct horse battery staple",
        mfa_secret=mfa_secret,
    )

    alert_client.post(
        "/auth/login",
        json={"subject": subject, "password": "wrong", "totp_code": pyotp.TOTP(mfa_secret).now()},
    )

    assert notifier.alerts == []


# --- cross-tenant probe -------------------------------------------------------


def test_repeated_not_found_lookups_fire_a_cross_tenant_probe_alert(
    alert_client: TestClient, notifier: FakeNotificationPort
) -> None:
    headers = auth_headers(Role.BILLER, "a")

    for _ in range(10):  # evaluate_cross_tenant_probe_alert's default threshold
        response = alert_client.get(f"/findings/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404

    probe_alerts = [a for a in notifier.alerts if a.name == "cross_tenant_probe"]
    assert len(probe_alerts) == 1


def test_a_single_not_found_lookup_does_not_fire_the_alert(
    alert_client: TestClient, notifier: FakeNotificationPort
) -> None:
    headers = auth_headers(Role.BILLER, "a")

    response = alert_client.get(f"/findings/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404
    assert notifier.alerts == []


def test_a_real_finding_lookup_never_fires_the_probe_alert(
    alert_client: TestClient, notifier: FakeNotificationPort, seed_ids: SeedIds
) -> None:
    """Sanity check: hitting a real, own-tenant finding repeatedly (not a
    404) never trips the not-found tracker at all."""
    headers = auth_headers(Role.BILLER, "a")

    for _ in range(10):
        response = alert_client.get(f"/findings/{seed_ids.finding_a}", headers=headers)
        assert response.status_code == 200

    assert notifier.alerts == []
