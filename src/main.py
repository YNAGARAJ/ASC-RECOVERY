"""Production composition root.

Reads configuration from environment variables via
`security.secrets.EnvSecretStore` and wires real adapters
(`PostgresRepository`, `AnthropicPacketDrafter`, real OTel exporters)
into a runnable ASGI app.

No cloud SDK code lives here or anywhere else in this codebase --
Terraform (see `terraform/`) provisions the actual secrets
store/KMS/database; a real orchestration platform (ECS task
definitions, Container Apps secrets, etc.) materializes secrets from
that store as environment variables before this process starts. This
module only ever reads already-materialized environment variables, the
same integration point every real deployment uses.

`create_app_from_env()` has no module-level side effect (no bare
`app = create_app_from_env()` at import time) specifically so it stays
directly testable -- `tests/test_main.py` calls it with env vars set via
`monkeypatch` and asserts on both the success and missing-config paths
without any import-order fragility. The container instead runs it via
uvicorn's factory mode: `uvicorn main:create_app_from_env --factory`
(see `Dockerfile`) -- app construction is deferred to server startup,
not import time, which is exactly what an ASGI factory is for.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter

from api.app import create_app
from api.repository import PostgresRepository
from db.base import make_engine, make_session_factory
from observability.logging_config import configure_logging
from observability.metrics import setup_metrics
from observability.notifications import LoggingNotificationPort
from observability.tracing import setup_tracing
from packets.drafter import AnthropicPacketDrafter
from security.encryption import EnvelopeEncryptor
from security.kms_env import EnvKMS
from security.secrets import EnvSecretStore, SecretNotFoundError


class MissingConfigurationError(RuntimeError):
    pass


def _require(secrets: EnvSecretStore, name: str) -> str:
    try:
        return secrets.get_secret(name)
    except SecretNotFoundError as exc:
        raise MissingConfigurationError(
            f"required environment variable {name} is not set"
        ) from exc


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


def create_app_from_env() -> FastAPI:
    # F-10 (docs/audit/REGISTER.md): first, before anything else has a
    # chance to log a line that isn't covered by any filter yet.
    configure_logging()

    secrets = EnvSecretStore()
    database_url = _require(secrets, "DATABASE_URL")
    jwt_secret_key = _require(secrets, "JWT_SECRET_KEY")
    anthropic_api_key = _require(secrets, "ANTHROPIC_API_KEY")
    _require(secrets, "PHI_ENCRYPTION_KEY")  # validated eagerly by EnvKMS below
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    # F-08/F-09 (docs/audit/REGISTER.md): both `tracer` and `instruments`
    # must actually reach `PostgresRepository` below, or every ingestion
    # metric and the one span in this codebase silently go to a no-op
    # provider -- a real deploy would look instrumented (Phase 8 built
    # all of this) while emitting nothing.
    tracer = setup_tracing(_span_exporter(otlp_endpoint), set_global=True)
    instruments = setup_metrics(
        PeriodicExportingMetricReader(_metric_exporter(otlp_endpoint))
    )

    session_factory = make_session_factory(make_engine(database_url))
    # F-13 (docs/audit/REGISTER.md): packets.prompt now keeps every claim
    # identifier (name, member id, claim control number, date of service)
    # out of the literal prompt text sent here -- but that's data
    # minimization, not a substitute for an actual BAA with the LLM
    # vendor. Do not point ANTHROPIC_API_KEY at a real account for a real
    # tenant until one exists (docs/compliance/README.md's checklist).
    drafter = AnthropicPacketDrafter(anthropic_api_key, instruments=instruments)
    # EnvKMS is a stopgap KEK store (see security/kms_env.py's docstring) --
    # a real cloud KMS adapter behind the same KeyManagementService port is
    # a named, deferred gap (docs/SECURITY.md), not yet built for lack of a
    # real cloud account to build it against.
    encryptor = EnvelopeEncryptor(EnvKMS(secrets))
    # F-11 (docs/audit/REGISTER.md): one shared notifier -- both the
    # repository (unusual PHI access, ingestion failure rate) and the
    # app's routes (auth anomaly, cross-tenant probe) dispatch alerts
    # through the same real adapter, real paging vendor still deferred
    # (see observability/notifications.py's docstring).
    notifier = LoggingNotificationPort()
    repository = PostgresRepository(
        session_factory,
        drafter=drafter,
        encryptor=encryptor,
        tracer=tracer,
        instruments=instruments,
        notifier=notifier,
    )

    return create_app(repository=repository, jwt_secret_key=jwt_secret_key, notifier=notifier)
