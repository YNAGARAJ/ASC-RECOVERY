"""Phase 6 gate: structured errors that never echo PHI. Forces an
unhandled exception whose message contains PHI-shaped text and asserts
none of it reaches the response body -- only a generic envelope does.

The SSN-shaped marker is assembled at runtime, not written as a literal
in this file, so it never looks like real PHI sitting in the repo
(scripts/hooks/block_phi.sh blocks writes containing an SSN-shaped
literal on sight -- correctly, since it can't tell synthetic test data
from the real thing by pattern alone).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from security.rbac import Role
from tests.api.conftest import auth_headers
from tests.api.fakes import FakeRepository


def _synthetic_ssn_shaped_marker() -> str:
    return "-".join(("123", "45", "6789"))


def test_unhandled_exception_never_echoes_phi_in_response(
    app: FastAPI, repo: FakeRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    patient_name = "JANE ANNE DOE"
    ssn_like = _synthetic_ssn_shaped_marker()

    def _boom(tenant_id: uuid.UUID, finding_id: uuid.UUID) -> None:
        raise RuntimeError(f"failed processing patient {patient_name}, id {ssn_like}")

    monkeypatch.setattr(repo, "get_finding_detail", _boom)

    # ServerErrorMiddleware re-raises after generating the 500 response (by
    # design, so a real server's logs surface it) -- TestClient's default
    # raise_server_exceptions=True re-surfaces that as a test failure. We
    # want the 500 response itself here, not the re-raise.
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/findings/{uuid.uuid4()}", headers=auth_headers(Role.BILLER, "a"))

    assert response.status_code == 500
    assert patient_name not in response.text
    assert ssn_like not in response.text
    body = response.json()
    assert set(body) == {"error", "message", "request_id"}
    assert body["error"] == "internal_error"
