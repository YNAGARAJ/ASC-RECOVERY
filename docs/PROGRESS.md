# Progress checkpoint

Written for a fresh session with no memory of prior conversation. Repo is
at a clean commit as of this checkpoint (about to commit "Phase 9: cloud-
agnostic deployment"). Read `docs/PHASES.md` first for the phase
checklist — this file adds the texture that isn't in that summary.

## Phase: 9 code-complete. Verification ceiling is lower than every prior phase.

Phases 0–2 and 4 are fully done. Phases 3, 5, 6, 7, and 8 each have an
honest local-Postgres gap. **Phase 9 is different in kind, not just
degree**: its gate ("deploys clean to at least two clouds ... restore
rehearsed and timed") needs a real Docker install and real, billed
AWS/Azure accounts — not something achievable with local tooling at all,
and not something to attempt without explicit go-ahead even if
credentials existed (a real `terraform apply` is a costly, hard-to-reverse
action against shared/external infrastructure). The user's own plan is to
install Docker in a GitHub Codespace and test the full application there
— that will verify containerization and the composition root for real,
but not the Terraform/multi-cloud half, which needs a further, separate
decision about real cloud accounts.

Current phase per `docs/PHASES.md`: **Phase 9 — Cloud-agnostic
deployment**, code-complete, application-layer pieces (composition root,
health endpoints) fully tested, infrastructure-as-code (Dockerfile,
Terraform) manually reviewed but not tool-verified.

## Done (this session, Phase 9)

**The first real gap found**: no production entrypoint existed anywhere.
`api.app.create_app()` had only ever been called by tests, with a
`FakeRepository` or a locally-constructed `PostgresRepository` — nothing
wired a real repository + real LLM drafter + real observability
exporters from environment variables into something `uvicorn` could
serve. Building that was this phase's actual first task, not just
Docker/Terraform packaging around something that already ran.

- **`src/main.py`** — `create_app_from_env()`, the production composition
  root. Reads `DATABASE_URL`/`JWT_SECRET_KEY`/`ANTHROPIC_API_KEY` via
  `security.secrets.EnvSecretStore` (Phase 4, unchanged); constructs
  `PostgresRepository` + `AnthropicPacketDrafter`; wires
  `observability.setup_tracing`/`setup_metrics` with a console exporter
  by default or a real OTLP exporter if `OTEL_EXPORTER_OTLP_ENDPOINT` is
  set (new `opentelemetry-exporter-otlp-proto-http` dependency — real
  integration code, untested against a live collector, same honest
  status as every other real-cloud-integration gap this build has
  named). **No module-level `app = create_app_from_env()`** — the
  function has zero import-time side effects specifically so
  `tests/test_main.py` can test both the missing-config and
  successful-construction paths without import-order fragility; the
  container instead runs it via uvicorn's ASGI factory mode
  (`uvicorn main:create_app_from_env --factory`).
- **`GET /healthz` / `GET /readyz`** (`src/api/routes/health.py`) — kept
  deliberately separate: `/healthz` never checks a dependency (a
  container orchestrator should never restart a healthy process just
  because the DB blipped); `/readyz` calls the new `Repository.ping()`
  and returns 503 on any failure (a load balancer pulls a not-ready
  instance from rotation without killing it). Both public, no auth —
  infra probes don't carry a bearer token. Fully tested
  (`tests/api/test_health.py`) plus one live-Postgres round-trip
  (`tests/api/test_endpoints_live_db.py::test_readyz_returns_200_against_real_postgres`).
- **`PostgresRepository` now threads `tracer`/`instruments` through to
  `ingestion.pipeline.ingest_file`** — a real wiring gap caught while
  building the composition root: Phase 8 built the instrumentation but
  `ingest_remittance` never actually passed it through, so every
  ingestion via the API was silently using the no-op defaults regardless
  of what `src/main.py` set up. Both are optional constructor kwargs,
  defaulting to `None` (no-op), so no existing caller broke.
- **`pyproject.toml` dependencies split**: `[project].dependencies` now
  holds everything the running app actually needs at runtime (sqlalchemy,
  alembic, psycopg, cryptography, pyjwt, pyotp, fastapi, uvicorn,
  pydantic, python-multipart, anthropic, opentelemetry-*); the `dev`
  extra is trimmed to lint/test-only tooling (pytest, pytest-cov, ruff,
  mypy, httpx, openapi-spec-validator). Previously *everything* lived
  under `dev`, because nothing had a runnable production entrypoint to
  care about the distinction. This is what lets the Docker image's
  `pip install .` skip installing pytest/ruff/mypy into production.
- **`Dockerfile`** — multi-stage (builder installs into a venv, final
  stage copies only the venv + `src/`/`alembic/`), non-root user (fixed
  UID/GID, not `adduser --system`, so Terraform/K8s security contexts
  have a stable value to reference), ASGI factory entrypoint. Base image
  tag (`python:3.12-slim`) is **not** pinned to a digest — that requires
  `docker pull`+`docker inspect` against a real registry, unavailable
  here; a fabricated-looking `sha256:...` would be actively misleading,
  so the Dockerfile instead comments the exact command to run once
  Docker exists (the user's planned Codespace).
- **`docker-compose.yml`** extended (not replaced) with an `app` service
  — builds from the new Dockerfile, connects as `asc_app` (never
  `asc_owner`), depends on `postgres` being healthy. Migrations are
  **not** run automatically on container start (documented as a
  deliberate anti-pattern once there's more than one replica — see
  `docs/RUNBOOK.md`).
- **`terraform/`** — a documented provider-agnostic *contract* (same
  input variables, same output values), not a shared runtime module
  (Terraform can't make one resource block conditionally become
  `aws_db_instance` vs. `azurerm_postgresql_flexible_server` at plan
  time — separate per-cloud modules satisfying an identical interface is
  the correct pattern, not a workaround). **AWS and Azure, not three
  clouds** — the gate says "at least two"; a third GCP module following
  the same pattern is real future work, but every additional line of
  unverifiable HCL here carries real risk with no `terraform validate`
  to catch a mistake. Each module provisions: network (VPC/VNet, private
  subnets, NAT, security groups — nothing PHI-bearing in a public
  subnet), Postgres (RDS / Flexible Server, encrypted, private, automated
  backups), object storage (S3 / Blob Storage, public access blocked at
  the account/storage-account level, not just per-bucket), secrets
  (Secrets Manager / Key Vault), KMS (KMS / Key Vault Keys, rotation
  enabled), container runtime (ECS Fargate + ALB / Container Apps) — every
  resource type is on that provider's published HIPAA-eligible-services
  list (`terraform/README.md`'s citation table). **No new Python cloud
  SDK code** — Terraform provisions the actual KMS key/secrets
  store/bucket; the app still only ever talks to `EnvSecretStore`/
  `LocalKMS`, same as every prior phase's real-cloud-adapter deferral.
- **`docs/RUNBOOK.md`** — deploy, rollback, restore (procedure written;
  "rehearsed and timed" needs a real database, flagged not faked), key
  rotation (reuses `EnvelopeEncryptor.rotate_kek()`, Phase 4), incident
  response, breach notification (60-day clock — process/legal work,
  Phase 11 scope, not templated here), and zero-downtime migrations
  (expand/contract — already this repo's practiced pattern; migrations
  `0002`-`0004` were all additive-only before this was ever written down).

## Failing

Nothing. `pytest -q` → **374 passed, 20 skipped** (11 `tests/db/`, 4
`tests/ingestion/`, 5 `tests/api/test_endpoints_live_db.py` — all honest
skips, one more than Phase 8's checkpoint for the new `/readyz` live
test). `mypy --strict .` and `ruff check .` clean across **132 source
files** (was 128). `python -m evals.run` → 100%/100%/100%/100%,
unaffected. `bandit -r . -x ./tests,./evals` clean. Branch coverage on
`domain/variance.py` still 100%.

**What could not be run, and why, stated plainly**: `docker build`/`docker
run` (no Docker in this environment); `terraform fmt`/`validate`/`plan`/
`apply` (no Terraform CLI, and `apply` additionally needs real cloud
accounts). In place of that tooling: manual line-by-line review of every
`.tf` file, plus a brace-balance and quote-balance check across all of
them (a real check, not theater — it would have caught an unclosed block
or string, which is the most common class of copy-paste HCL mistake, even
though it can't catch a wrong resource argument name or type mismatch the
way `terraform validate` would).

## Decisions worth knowing (not obvious from the code)

- **`create_app_from_env()` has no module-level side effect, on
  purpose.** A bare `app = create_app_from_env()` at import time is the
  usual `uvicorn main:app` pattern, but it would mean simply *importing*
  `main` fails hard if env vars aren't set — awkward for testing both the
  missing-config and success paths in the same test session. uvicorn's
  `--factory` flag (`uvicorn main:create_app_from_env --factory`) defers
  construction to server startup instead of import time; this is a
  first-class, documented uvicorn feature, not a workaround.
- **`PostgresRepository.ingest_remittance` never passed `tracer`/
  `instruments` to `ingest_file`, discovered while building the
  composition root.** Phase 8 built real instrumentation and wired it
  into `ingestion.pipeline.ingest_file`'s signature, but the one and only
  caller inside the API layer never actually passed anything through —
  so every ingestion via the API silently used the no-op defaults
  regardless of what got configured upstream. Fixed by threading both
  through `PostgresRepository.__init__`. Worth checking for the same
  pattern (a Phase N instrument built but never reaching its Phase N+1
  caller) if `packets.drafter`'s `Instruments` wiring is ever extended.
- **FastAPI's `app.routes` doesn't reflect included routers directly in
  the installed version (0.141.1)** — `_IncludedRouter` wrapper objects
  show up instead of flattened `APIRoute`s until the app actually
  serves a request or generates its OpenAPI schema. `tests/test_main.py`
  checks `app.openapi()["paths"]` instead of walking `app.routes`,
  matching how `tests/api/test_openapi.py` already did it. If a future
  test introspects `app.routes` directly and routes seem to have
  "vanished," this is why — it's a FastAPI-version quirk, not a routing
  bug.
- **`pyproject.toml`'s dependency split was overdue, not new scope
  invented for this phase.** Every dependency added in Phases 6-8
  (fastapi, anthropic, opentelemetry-*, ...) is something the app
  actually needs to run, not just to test — but they all went into `dev`
  because, until `src/main.py` existed, there was no "run" to distinguish
  from "test." This phase's Dockerfile is what finally made the
  distinction matter.
- **Terraform's two secrets-manager-shaped resources per cloud
  (`app_database_url` + the generic `app` secret for JWT/Anthropic keys)
  exist because RDS's `manage_master_user_password` only covers the
  master (`asc_owner`) user.** The `asc_app` runtime role doesn't exist
  until the migration step creates it (`docs/RUNBOOK.md`), so Terraform
  generates its password via `random_password` and pre-assembles the
  full `DATABASE_URL` into its own secret, read back via a
  `data "aws_secretsmanager_secret_version"` lookup on the master
  secret to build the connection string. The Azure module does the
  equivalent with Key Vault. If `scripts/db/init_roles.sql` is ever run
  against real cloud infrastructure, its hardcoded local-dev password
  for `asc_app` must be swapped for the Terraform-generated one first —
  called out explicitly in `docs/RUNBOOK.md`'s step 3.

## Traps for someone resuming cold

- Everything from the Phase 3–8 checkpoints still applies (no
  project-local virtualenv, CRLF warnings on `git add`, generated/ruff-
  excluded `evals/golden/cases.py`, SSN-shaped test fixtures assembled at
  runtime to avoid tripping `block_phi.sh`).
- **Don't trust the Terraform to be free of mistakes just because brace/
  quote balance checked out.** That check catches unclosed blocks/strings
  only — it says nothing about whether `azurerm_container_app`'s `secret`
  block schema is exactly right, or whether an argument name drifted from
  the provider version pinned in `versions.tf`. Run `terraform validate`
  as the very first step once the CLI exists, before trusting any of
  this against a real account.
- **`docker-compose.yml`'s `app` service will fail on a truly fresh
  `docker compose up`** until migrations are run — this is intentional
  (see `docs/RUNBOOK.md`'s "migrations are never automatic" decision),
  not a bug to "fix" by adding an entrypoint script that runs Alembic.
- **`terraform/modules/{aws,azure}` were authored without ever running
  `terraform fmt`** — expect inconsistent alignment/whitespace in a few
  spots; purely cosmetic, `terraform fmt` will normalize it instantly
  once available, don't hand-align it first.

## Next 3 steps

1. **In the Codespace**: install Docker, run `docker compose up -d`,
   then `docker compose run --rm app alembic upgrade head` (or equivalent
   — see `docs/RUNBOOK.md`), then exercise `/healthz`, `/readyz`, and a
   real upload-through-packet-approval flow against the running stack.
   If that all works, the Docker/composition-root half of Phase 9 is
   genuinely verified — update `docs/PHASES.md` to say so specifically
   (not "Phase 9 done," since Terraform is a separate, still-open half).
2. **Separately, if/when real AWS and Azure accounts become available**:
   run `terraform validate` first (catches real mistakes this session's
   brace-balance check can't), then `plan`, then — only with explicit
   sign-off, since `apply` is billed and not trivially reversible —
   `apply` against a disposable sandbox account before anything that
   matters. Rehearse and time a restore per `docs/RUNBOOK.md` before
   checking off the phase's actual gate.
3. **Otherwise, start Phase 10 (CI/CD and pre-production hardening)** per
   `docs/MASTER-BUILD-PROMPT.md`: pipeline (lint -> type check -> unit ->
   integration -> eval -> security scan -> container scan -> IaC scan ->
   staging -> smoke test -> manual gate -> production), SBOM generation,
   full-history secret scanning, and running the `adversarial-reviewer`
   subagent across the whole codebase with a fix-everything-HIGH-or-above
   requirement — a good fit for whenever Phase 9's Docker/Terraform
   verification is still pending, since CI/CD pipeline *design* doesn't
   itself require the infrastructure to exist yet. Enter plan mode first,
   same as every prior phase.
