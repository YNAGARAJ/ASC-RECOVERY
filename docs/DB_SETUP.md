# Database setup (Phase 3, access model updated in Phase 4)

## Status: code complete, gate NOT yet verified

This code (models, migration, the org/facility/membership access model,
repository, `tests/db/`) was written without a live Postgres available in
the build environment. `mypy --strict`, `ruff`, and offline Alembic SQL
generation are clean, but the actual hard gate -- Row-Level Security
blocking a cross-facility read at the database level, resolved through
the org hierarchy -- has **not** been run against a real database.
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

**`asc_owner` must have the `BYPASSRLS` attribute** (Phase 4,
`alembic/versions/0001_initial_schema.py`'s own docstring has the full
reasoning): `resolve_accessible_facility_ids`/`resolve_accessible_org_ids`
are `SECURITY DEFINER` functions owned by whoever runs the migration, and
they need to walk `organizations`/`facilities`/`memberships` internally
without being blocked by those same tables' own RLS policies. Docker's
bootstrap `POSTGRES_USER` is already a full superuser by convention, so
this is automatically satisfied here -- nothing to do for the
`docker compose` path. A manually-provisioned real Postgres install
needs this granted explicitly once, as a real superuser:
```sql
CREATE ROLE asc_owner LOGIN PASSWORD '...' CREATEDB BYPASSRLS;
```
(or `ALTER ROLE asc_owner BYPASSRLS;` if the role already exists). This
is the same tier of one-time, human-run setup as creating the role and
database in the first place -- not something any automated pipeline
does.

These are local-dev-only passwords, not secrets -- the container is not
exposed beyond localhost. Real secret management is Phase 4/9 scope.

## Run migrations

```bash
export DATABASE_URL="postgresql+psycopg://asc_owner:asc_owner_dev_password@localhost:5432/asc_recovery?sslmode=disable"
alembic upgrade head
```

`sslmode=disable` is explicit, not an oversight -- `db.base.make_engine`
(F-07, `docs/audit/REGISTER.md`) now defaults to `sslmode=require`
whenever a `DATABASE_URL` omits `sslmode` entirely, and this local
`docker compose` Postgres has no TLS configured at all.

Migrations run as `asc_owner` (the table owner) so `GRANT`/`REVOKE` and
`ENABLE ROW LEVEL SECURITY` statements succeed.

## Run the DB test suite

```bash
export TEST_DATABASE_URL="postgresql+psycopg://asc_app:asc_app_dev_password@localhost:5432/asc_recovery?sslmode=disable"
export DATABASE_URL="postgresql+psycopg://asc_owner:asc_owner_dev_password@localhost:5432/asc_recovery?sslmode=disable"
pytest tests/db/ -v
```

`tests/db/conftest.py` skips every test in this directory with an explicit
message if `TEST_DATABASE_URL` is unset -- it never silently passes. The
suite connects as `asc_app` for the actual reads/writes under test
(matching how the application will connect in production, so the RLS
test is a genuine proof rather than a superuser false-pass), but seeding
a fresh org/facility/user/membership needs the owner connection
(`DATABASE_URL`, falls back to `TEST_DATABASE_URL` if unset --
`tests/db/conftest.py`'s `owner_engine` fixture) -- `asc_app` cannot
create these itself; see the `BYPASSRLS` note above for why.

## What to look for

- `tests/db/test_rls_tenant_isolation.py` is the hard gate, now five
  proofs deep (Phase 4): an unfiltered `SELECT * FROM claims` while
  `app.user_id` is set to a user with access to facility A returns zero
  of facility B's rows, and the same unfiltered query **would** return
  both facilities' rows with RLS forced off -- proving RLS, not empty
  test data, is what's blocking it. Plus: a billing-company user scoped
  to two specific facilities can't read a third, a parent-org membership
  reaches a child-org's facility, revoking a membership blocks access on
  the very next query, and a five-level org hierarchy resolves (and
  terminates) correctly. It also proves `analyst`'s field-level PHI
  masking end to end against a real decrypted claim.
- `tests/db/test_rls_coverage.py` proves every table except the
  deliberately-ungated bootstrap set (`users`) has RLS enabled, forced,
  and a policy -- coverage, not correctness (that's the file above).
- `tests/db/test_idempotent_remittance.py` proves re-ingesting an identical
  file hash creates no new claims/findings.
- `tests/db/test_effective_dated_pricing.py` proves a claim dated last year
  prices against last year's contract version once round-tripped through
  Postgres.
- `tests/db/test_audit_log_append_only.py` proves `asc_app` has no
  `UPDATE`/`DELETE` grant on `audit_log`.

Once these pass, update `docs/PHASES.md` to check off the relevant phases.

## Tearing down

```bash
docker compose down -v   # -v also drops the data volume
```
