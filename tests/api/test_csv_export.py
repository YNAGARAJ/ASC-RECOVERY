"""CSV worklist export -- format correctness beyond what the authz matrix
already covers (which only checks tenant isolation)."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from security.rbac import Role
from tests.api.conftest import auth_headers


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
