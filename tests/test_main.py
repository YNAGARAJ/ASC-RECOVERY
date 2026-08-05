"""Production composition root: env-var validation and app construction,
without touching a real database (`create_engine` doesn't connect until
first use) or a real LLM (constructing the Anthropic client doesn't call
the network either) -- fully testable without any live infrastructure.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from main import MissingConfigurationError, create_app_from_env

_REQUIRED_VARS = ("DATABASE_URL", "JWT_SECRET_KEY", "ANTHROPIC_API_KEY", "PHI_ENCRYPTION_KEY")

# base64 of 32 arbitrary bytes -- shaped correctly for EnvKMS's length check,
# not a real key (see security/kms_env.py).
_TEST_PHI_ENCRYPTION_KEY = "dGVzdC1rZXktbm90LXJlYWwtMzItYnl0ZXMtbG9uZyE="


def _set_all_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/asc")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _TEST_PHI_ENCRYPTION_KEY)


@pytest.mark.parametrize("missing_var", _REQUIRED_VARS)
def test_missing_required_env_var_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    _set_all_required(monkeypatch)
    monkeypatch.delenv(missing_var)

    with pytest.raises(MissingConfigurationError, match=missing_var):
        create_app_from_env()


def test_all_required_vars_present_constructs_a_working_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_required(monkeypatch)

    app = create_app_from_env()

    assert isinstance(app, FastAPI)
    paths = set(app.openapi()["paths"])
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_otlp_exporter_path_constructs_cleanly_when_endpoint_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No real collector is contacted at construction time -- OTLP
    exporters only connect when something is actually exported."""
    _set_all_required(monkeypatch)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")

    app = create_app_from_env()

    assert isinstance(app, FastAPI)
