"""Tests for F-07's HSTS header (docs/audit/REGISTER.md) --
api/security_headers.py's SecurityHeadersMiddleware."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_every_response_carries_hsts(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.headers["strict-transport-security"] == (
        "max-age=63072000; includeSubDomains"
    )


def test_hsts_is_present_on_an_error_response(client: TestClient) -> None:
    # Missing totp_code -- a 422, and it needs no auth header to reach it.
    response = client.post("/auth/login", json={"subject": "x", "password": "y"})
    assert response.status_code == 422
    assert "strict-transport-security" in response.headers


def test_hsts_is_present_on_an_unauthenticated_401(client: TestClient) -> None:
    response = client.get("/findings")
    assert response.status_code == 401
    assert "strict-transport-security" in response.headers
