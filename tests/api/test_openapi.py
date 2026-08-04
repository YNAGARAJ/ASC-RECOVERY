"""Phase 6 gate: 'OpenAPI spec generated' and 'OpenAPI validates'."""

from __future__ import annotations

from fastapi import FastAPI
from openapi_spec_validator import validate


def test_openapi_spec_is_generated_and_valid(app: FastAPI) -> None:
    schema = app.openapi()
    assert schema["info"]["title"] == "ASC Underpayment Recovery API"
    validate(schema)


def test_openapi_covers_every_expected_path(app: FastAPI) -> None:
    schema = app.openapi()
    paths = set(schema["paths"])
    expected = {
        "/remittances",
        "/findings",
        "/findings/export.csv",
        "/findings/{finding_id}",
        "/contracts",
        "/contracts/{contract_id}/versions",
        "/audit-log",
        "/findings/{finding_id}/packets",
        "/packets/{packet_id}/approve",
        "/packets/{packet_id}/reject",
    }
    assert expected <= paths
