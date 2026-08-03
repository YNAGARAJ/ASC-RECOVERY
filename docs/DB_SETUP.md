# Database setup (Phase 3)

## Status: code complete, RLS gate NOT yet verified

The Phase 3 code (models, migration, tenancy plumbing, repository,
`tests/db/`) was written without a live Postgres available in the build
environment. `mypy --strict`, `ruff`, and offline Alembic SQL generation are
clean, but the actual hard gate -- Row-Level Security blocking a cross-tenant
read at the database level -- has **not** been run against a real database.
See `docs/PHASES.md` for the current phase checklist. Follow the steps below
to finish verification.

## Bring up Postgres

```bash
docker compose up -d
docker compose ps  # wait for postgres to report healthy
```

This starts Postgres 16 with database `asc_recovery`, owned by `asc_owner`
(password `asc_owner_dev_password`, see `docker-compose.yml`). On first
start it also runs `scripts/db/init_roles.sql`, which creates the
**application runtime role `asc_app`** (password `asc_app_dev_password`,
see that file) -- a non-superuser, non-`BYPASSRLS` role. Every table grant,
RLS policy, and the audit_log append-only restriction are set up in the
Alembic migration itself, scoped to `asc_app`.

These are local-dev-only passwords, not secrets -- the container is not
exposed beyond localhost. Real secret management is Phase 4/9 scope.

## Run migrations

```bash
export DATABASE_URL="postgresql+psycopg://asc_owner:asc_owner_dev_password@localhost:5432/asc_recovery"
alembic upgrade head
```

Migrations run as `asc_owner` (the table owner) so `GRANT`/`REVOKE` and
`ENABLE ROW LEVEL SECURITY` statements succeed.

## Run the DB test suite

```bash
export TEST_DATABASE_URL="postgresql+psycopg://asc_app:asc_app_dev_password@localhost:5432/asc_recovery"
pytest tests/db/ -v
```

`tests/db/conftest.py` skips every test in this directory with an explicit
message if `TEST_DATABASE_URL` is unset -- it never silently passes. The
suite connects as `asc_app`, matching how the application will connect in
production, so the RLS test is a genuine proof rather than a superuser
false-pass.

## What to look for

- `tests/db/test_rls_tenant_isolation.py` is the hard gate: it must show
  that an unfiltered `SELECT * FROM claims` while `app.tenant_id` is set to
  tenant A returns zero of tenant B's rows, and that the same unfiltered
  query **would** return both tenants' rows with RLS forced off -- proving
  RLS, not empty test data, is what's blocking it.
- `tests/db/test_idempotent_remittance.py` proves re-ingesting an identical
  file hash creates no new claims/findings.
- `tests/db/test_effective_dated_pricing.py` proves a claim dated last year
  prices against last year's contract version once round-tripped through
  Postgres.
- `tests/db/test_audit_log_append_only.py` proves `asc_app` has no
  `UPDATE`/`DELETE` grant on `audit_log`.

Once all four pass, update `docs/PHASES.md` to check off Phase 3.

## Tearing down

```bash
docker compose down -v   # -v also drops the data volume
```
