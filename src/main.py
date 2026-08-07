"""Production composition root -- the API entrypoint. `src/worker.py`
(Phase 7, `docs/MASTER-BUILD-PROMPT-V2.md`) is the other one, sharing
`src/composition.py`'s environment-driven adapter construction so
neither duplicates the `KMS_PROVIDER` switch, OTLP exporter selection,
or secrets validation.

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

from api.app import create_app
from api.repository import PostgresRepository
from composition import (
    # "as MissingConfigurationError": mypy --strict's no-implicit-reexport
    # needs this exact self-alias to treat it as a deliberate re-export --
    # tests/test_main.py imports it from here, not from composition.py.
    MissingConfigurationError as MissingConfigurationError,
)
from composition import (
    build_encryptor,
    build_notifier,
    build_observability,
    require_secret,
)
from db.base import make_engine, make_session_factory
from observability.logging_config import configure_logging
from packets.drafter import AnthropicPacketDrafter
from security.secrets import EnvSecretStore


def create_app_from_env() -> FastAPI:
    # F-10 (docs/audit/REGISTER.md): first, before anything else has a
    # chance to log a line that isn't covered by any filter yet.
    configure_logging()

    secrets = EnvSecretStore()
    database_url = require_secret(secrets, "DATABASE_URL")
    jwt_secret_key = require_secret(secrets, "JWT_SECRET_KEY")
    anthropic_api_key = require_secret(secrets, "ANTHROPIC_API_KEY")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    tracer, instruments = build_observability(otlp_endpoint)

    session_factory = make_session_factory(make_engine(database_url))
    # F-13 (docs/audit/REGISTER.md): packets.prompt now keeps every claim
    # identifier (name, member id, claim control number, date of service)
    # out of the literal prompt text sent here -- but that's data
    # minimization, not a substitute for an actual BAA with the LLM
    # vendor. Do not point ANTHROPIC_API_KEY at a real account for a real
    # tenant until one exists (docs/compliance/README.md's checklist).
    drafter = AnthropicPacketDrafter(anthropic_api_key, instruments=instruments)
    encryptor = build_encryptor(secrets)
    notifier = build_notifier()
    repository = PostgresRepository(
        session_factory,
        drafter=drafter,
        encryptor=encryptor,
        tracer=tracer,
        instruments=instruments,
        notifier=notifier,
    )

    return create_app(repository=repository, jwt_secret_key=jwt_secret_key, notifier=notifier)
