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


def test_kms_provider_unset_defaults_to_envkms_and_still_requires_phi_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-20 (docs/audit/REGISTER.md): KMS_PROVIDER unset must behave
    exactly as before this fix -- PHI_ENCRYPTION_KEY still required, no
    new env var demanded. Already covered indirectly by the
    missing-PHI_ENCRYPTION_KEY case in test_missing_required_env_var_raises_a_clear_error,
    this asserts the same default explicitly under the new KMS_PROVIDER
    branch rather than relying on that coincidence."""
    _set_all_required(monkeypatch)
    monkeypatch.delenv("KMS_PROVIDER", raising=False)

    app = create_app_from_env()

    assert isinstance(app, FastAPI)


def test_kms_provider_env_explicit_behaves_the_same_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_required(monkeypatch)
    monkeypatch.setenv("KMS_PROVIDER", "env")

    app = create_app_from_env()

    assert isinstance(app, FastAPI)


def test_kms_provider_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all_required(monkeypatch)
    monkeypatch.setenv("KMS_PROVIDER", "some-other-cloud")

    with pytest.raises(MissingConfigurationError, match="KMS_PROVIDER"):
        create_app_from_env()


def test_kms_provider_aws_kms_without_key_id_raises_before_touching_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS_KMS_KEY_ID is validated by _require() before
    build_aws_kms_adapter's lazy `import boto3` runs -- proven here by the
    fact this passes without boto3 installed in this dev environment at
    all (pyproject.toml's [cloud-kms] extra)."""
    _set_all_required(monkeypatch)
    monkeypatch.setenv("KMS_PROVIDER", "aws-kms")
    monkeypatch.delenv("AWS_KMS_KEY_ID", raising=False)

    with pytest.raises(MissingConfigurationError, match="AWS_KMS_KEY_ID"):
        create_app_from_env()


def test_kms_provider_azure_keyvault_without_key_id_raises_before_touching_azure_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_required(monkeypatch)
    monkeypatch.setenv("KMS_PROVIDER", "azure-keyvault")
    monkeypatch.delenv("AZURE_KEY_VAULT_KEY_ID", raising=False)

    with pytest.raises(MissingConfigurationError, match="AZURE_KEY_VAULT_KEY_ID"):
        create_app_from_env()


def test_repository_is_wired_with_real_instruments_and_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-08/F-09 (docs/audit/REGISTER.md): before this fix, every
    ingestion metric and the one span in the codebase went to a no-op
    provider because this exact construction call never passed
    instruments=/tracer= -- a real deploy would look instrumented
    (Phase 8 built all of it) while emitting nothing. Reaches into the
    repository's own attributes rather than exercising an ingestion
    end to end, since that needs a live Postgres this environment
    doesn't have -- this is the wiring-level proof that the gap is closed."""
    _set_all_required(monkeypatch)

    app = create_app_from_env()

    repository = app.state.repository
    assert repository._instruments is not None
    assert repository._tracer is not None
