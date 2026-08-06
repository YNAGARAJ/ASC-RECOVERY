#!/usr/bin/env bash
# Bootstraps the asc_app runtime role (creating it if it doesn't exist yet,
# or resetting its password if it does) with the real, Terraform-generated
# password, then runs Alembic migrations -- both connected as the
# table-owning asc_owner role. Called from .github/workflows/deploy.yml
# right after `terraform apply`, before anything smoke-tests the service.
#
# Without this step a fresh deploy's database has an unmigrated schema and
# no asc_app role at all, while the old health-check-only smoke test still
# reported green -- see F-03, docs/audit/REGISTER.md.
set -euo pipefail

: "${OWNER_DATABASE_URL:?OWNER_DATABASE_URL must be set (asc_owner connection string, postgresql+psycopg:// form)}"
: "${ASC_APP_PASSWORD:?ASC_APP_PASSWORD must be set (the real, Terraform-generated asc_app password)}"

# psql/libpq don't understand SQLAlchemy's "+psycopg" dialect suffix that
# DATABASE_URL (and this script's own OWNER_DATABASE_URL) uses everywhere
# else in this repo -- strip it for the raw psql connection only.
PSQL_URL="${OWNER_DATABASE_URL/postgresql+psycopg:/postgresql:}"

# random_password.app_db (both Terraform modules) sets special = false, so
# ASC_APP_PASSWORD is always plain alphanumeric -- safe to interpolate
# directly into a single-quoted SQL literal below with no escaping.
psql "$PSQL_URL" <<SQL
DO
\$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'asc_app') THEN
        CREATE ROLE asc_app LOGIN PASSWORD '$ASC_APP_PASSWORD' NOSUPERUSER NOBYPASSRLS;
    ELSE
        ALTER ROLE asc_app WITH PASSWORD '$ASC_APP_PASSWORD';
    END IF;
END
\$\$;

GRANT CONNECT ON DATABASE asc_recovery TO asc_app;
GRANT USAGE ON SCHEMA public TO asc_app;
SQL

DATABASE_URL="$OWNER_DATABASE_URL" alembic upgrade head
