"""Shared environment-driven adapter construction for both production
entrypoints -- `src/main.py` (the API) and `src/worker.py` (Phase 7's
job queue worker, `docs/MASTER-BUILD-PROMPT-V2.md`). Neither entrypoint
duplicates this: the same `KMS_PROVIDER` switch, the same OTLP exporter
selection, the same secrets validation, regardless of which process
reads it.

No cloud SDK code lives here or anywhere else in this codebase --
Terraform (see `terraform/`) provisions the actual secrets
store/KMS/database; a real orchestration platform (ECS task
definitions, Container Apps secrets, etc.) materializes secrets from
that store as environment variables before either process starts. This
module only ever reads already-materialized environment variables, the
same integration point every real deployment uses.
"""

from __future__ import annotations

import os

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Tracer

from observability.metrics import Instruments, setup_metrics
from observability.notifications import LoggingNotificationPort, NotificationPort
from observability.tracing import setup_tracing
from security.encryption import EnvelopeEncryptor
from security.kms import KeyManagementService
from security.kms_env import EnvKMS
from security.secrets import EnvSecretStore, SecretNotFoundError


class MissingConfigurationError(RuntimeError):
    pass


def require_secret(secrets: EnvSecretStore, name: str) -> str:
    try:
        return secrets.get_secret(name)
    except SecretNotFoundError as exc:
        raise MissingConfigurationError(
            f"required environment variable {name} is not set"
        ) from exc


def build_kms(secrets: EnvSecretStore) -> KeyManagementService:
    """F-20 (docs/audit/REGISTER.md): opt-in only -- KMS_PROVIDER unset
    or "env" behaves exactly as before this fix (EnvKMS, PHI_ENCRYPTION_KEY
    required). Neither real cloud adapter has ever been exercised against
    a real KMS/Key Vault (see security/kms_aws.py, security/kms_azure.py's
    own docstrings), so switching a real deployment onto one is a
    deliberate operator choice, never a silent default change."""
    provider = os.environ.get("KMS_PROVIDER", "env")
    if provider == "env":
        require_secret(secrets, "PHI_ENCRYPTION_KEY")  # validated eagerly by EnvKMS below
        return EnvKMS(secrets)
    if provider == "aws-kms":
        from security.kms_aws import build_aws_kms_adapter

        key_id = require_secret(secrets, "AWS_KMS_KEY_ID")
        return build_aws_kms_adapter(key_id=key_id, region=os.environ.get("AWS_REGION"))
    if provider == "azure-keyvault":
        from security.kms_azure import build_azure_keyvault_adapter

        key_id = require_secret(secrets, "AZURE_KEY_VAULT_KEY_ID")
        return build_azure_keyvault_adapter(key_id=key_id)
    raise MissingConfigurationError(
        f"KMS_PROVIDER must be 'env', 'aws-kms', or 'azure-keyvault', got {provider!r}"
    )


def build_encryptor(secrets: EnvSecretStore) -> EnvelopeEncryptor:
    return EnvelopeEncryptor(build_kms(secrets))


def _span_exporter(otlp_endpoint: str | None) -> SpanExporter:
    if otlp_endpoint is None:
        # No real tracing backend configured -- write spans to stdout
        # rather than silently dropping them. A real OTLP collector is
        # Phase 9 deployment scope, not exercised in this environment;
        # this exporter choice is what makes that gap visible rather
        # than invisible.
        return ConsoleSpanExporter()
    return OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")


def _metric_exporter(otlp_endpoint: str | None) -> MetricExporter:
    if otlp_endpoint is None:
        return ConsoleMetricExporter()
    return OTLPMetricExporter(endpoint=f"{otlp_endpoint}/v1/metrics")


def build_observability(otlp_endpoint: str | None) -> tuple[Tracer, Instruments]:
    """F-08/F-09 (docs/audit/REGISTER.md): both the tracer and instruments
    this returns must actually reach whatever calls ingestion (both
    `PostgresRepository` in `src/main.py` and `src/jobs/runner.py`'s
    worker), or every ingestion metric and the one span in this codebase
    silently go to a no-op provider -- a real deploy would look
    instrumented (Phase 8 built all of it) while emitting nothing."""
    tracer = setup_tracing(_span_exporter(otlp_endpoint), set_global=True)
    instruments = setup_metrics(PeriodicExportingMetricReader(_metric_exporter(otlp_endpoint)))
    return tracer, instruments


def build_notifier() -> NotificationPort:
    # F-11 (docs/audit/REGISTER.md): one real adapter, shared by every
    # caller (API routes/repository, and now the worker) that dispatches
    # an Alert -- real paging vendor still deferred (see
    # observability/notifications.py's own docstring).
    return LoggingNotificationPort()
