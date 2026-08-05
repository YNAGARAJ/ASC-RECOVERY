"""/healthz (liveness) and /readyz (readiness) -- both public, no auth
required, since infra probes don't carry a bearer token."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from tests.api.fakes import FakeRepository


def test_healthz_returns_ok_with_no_auth_header(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_200_when_repository_is_healthy(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readyz_returns_503_when_repository_is_unhealthy(
    client: TestClient, repo: FakeRepository
) -> None:
    repo.healthy = False

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not ready"


class _ExplodingRepository(FakeRepository):
    def ping(self) -> bool:
        raise RuntimeError("connection refused")


def test_readyz_returns_503_when_ping_raises() -> None:
    app = create_app(repository=_ExplodingRepository(), jwt_secret_key="test-secret")
    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
