-- Runs once when the postgres container's data volume is first initialized
-- (mounted into /docker-entrypoint-initdb.d). Creates the application
-- runtime role that everything in src/db/ and tests/db/ connects as.
--
-- asc_app is deliberately NOT a superuser and has NO BYPASSRLS -- the whole
-- point of the RLS gate test is that this role has no way around the
-- tenant-isolation policies short of dropping them. Table-level grants for
-- asc_app happen in the Alembic migration, after the tables exist.

DO
$$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'asc_app') THEN
        CREATE ROLE asc_app LOGIN PASSWORD 'asc_app_dev_password' NOSUPERUSER NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE asc_recovery TO asc_app;
GRANT USAGE ON SCHEMA public TO asc_app;
