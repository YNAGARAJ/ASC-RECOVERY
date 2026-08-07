"""CSV worklist export -- format correctness beyond what the authz matrix
already covers (which only checks tenant isolation), plus Phase 6's
forced-re-auth gate (`api.auth.require_permission_with_recent_auth`,
`MASTER-BUILD-PROMPT-V2.md`)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from security.rbac import Role
from security.session import REAUTH_MAX_AGE, issue_session
from tests.api.conftest import JWT_SECRET, TENANT_A, auth_headers, subject_for


def _headers_authenticated_at(role: Role, *, authenticated_at: datetime) -> dict[str, str]:
    subject = subject_for(role, "a")
    tokens = issue_session(
        JWT_SECRET, subject, str(TENANT_A), mfa_verified=True, now=authenticated_at
    )
    return {"Authorization": f"Bearer {tokens.access_token}"}


def test_csv_has_header_and_one_data_row(client: TestClient) -> None:
    response = client.get("/findings/export.csv", headers=auth_headers(Role.BILLER, "a"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    reader = csv.reader(io.StringIO(response.text))
    rows = list(reader)
    assert rows[0] == [
        "finding_id",
        "claim_id",
        "procedure_code",
        "expected_allowed",
        "actual_allowed",
        "shortfall",
        "root_cause",
        "outcome",
        "amount_recovered",
    ]
    assert len(rows) == 2
    assert rows[1][2] == "99213"
    assert rows[1][5] == "50.00"
    assert rows[1][6] == "MPPR_NOT_APPLIED"


def test_csv_export_rejects_a_stale_session(client: TestClient) -> None:
    stale = datetime.now(UTC) - REAUTH_MAX_AGE - timedelta(minutes=1)
    headers = _headers_authenticated_at(Role.BILLER, authenticated_at=stale)

    response = client.get("/findings/export.csv", headers=headers)

    assert response.status_code == 401
    assert response.json()["message"] == "recent re-authentication required for this action"


def test_csv_export_succeeds_just_inside_the_reauth_window(client: TestClient) -> None:
    just_inside = datetime.now(UTC) - REAUTH_MAX_AGE + timedelta(seconds=30)
    headers = _headers_authenticated_at(Role.BILLER, authenticated_at=just_inside)

    response = client.get("/findings/export.csv", headers=headers)

    assert response.status_code == 200


def test_a_stale_session_can_still_list_findings(client: TestClient) -> None:
    """Forced re-auth is scoped to the export route only -- staleness
    doesn't lock a caller out of the rest of the API, only the one action
    this control actually guards."""
    stale = datetime.now(UTC) - REAUTH_MAX_AGE - timedelta(minutes=1)
    headers = _headers_authenticated_at(Role.BILLER, authenticated_at=stale)

    response = client.get("/findings", headers=headers)

    assert response.status_code == 200
