"""Phase 7 (`docs/MASTER-BUILD-PROMPT-V2.md`) production worker
entrypoint -- the polling loop, alongside `src/main.py`'s API entrypoint.
Run: `python -m worker` (`docker-compose.yml`'s `worker` service,
`terraform/modules/{aws,azure}`'s second task/container).

**Two database connections, not one** -- see `db.repository`'s "Jobs"
section docstring for the full reasoning:

- `DATABASE_URL` (same role `src/main.py` uses, `asc_app`) is what
  `src/jobs/runner.py` opens via `access_session` to actually execute a
  claimed job's business logic, re-establishing that job's own
  submitter's resolved access.
- `QUEUE_DATABASE_URL` (`asc_owner`, `BYPASSRLS`) drives the queue's own
  system-wide bookkeeping -- claim/progress/cancel-check/complete/fail --
  which is not any single user's resolved-access set. Same owner-role
  requirement `scripts/onboard_customer.py` already documents for its
  own system-level bootstrap work.

No `JWT_SECRET_KEY`/`ANTHROPIC_API_KEY` required here, unlike
`src/main.py` -- this process never issues auth tokens or drafts
recovery packets, only executes queued jobs.
"""

from __future__ import annotations

import os
import time

from composition import build_encryptor, build_notifier, build_observability, require_secret
from db.base import make_engine, make_session_factory
from ingestion.virus_scan import EicarAwareScanner
from jobs.runner import claim_and_run_once, next_worker_id
from observability.logging_config import configure_logging
from security.secrets import EnvSecretStore

_DEFAULT_POLL_INTERVAL_SECONDS = 5.0


def main() -> None:
    configure_logging()

    secrets = EnvSecretStore()
    database_url = require_secret(secrets, "DATABASE_URL")
    queue_database_url = require_secret(secrets, "QUEUE_DATABASE_URL")
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    poll_interval = float(
        os.environ.get("WORKER_POLL_INTERVAL_SECONDS", _DEFAULT_POLL_INTERVAL_SECONDS)
    )

    tracer, instruments = build_observability(otlp_endpoint)
    app_session_factory = make_session_factory(make_engine(database_url))
    owner_session_factory = make_session_factory(make_engine(queue_database_url))
    encryptor = build_encryptor(secrets)
    # Same adapter api/routes/remittances.py's upload path already uses
    # -- see that module's own note on this being the real, if narrow,
    # scanner this codebase has, not a test-only stub.
    scanner = EicarAwareScanner()
    notifier = build_notifier()
    worker_id = next_worker_id()

    while True:
        did_work = claim_and_run_once(
            owner_session_factory,
            app_session_factory,
            worker_id=worker_id,
            encryptor=encryptor,
            scanner=scanner,
            tracer=tracer,
            instruments=instruments,
            notifier=notifier,
        )
        if not did_work:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
