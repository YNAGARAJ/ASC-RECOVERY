from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from db import models  # noqa: F401  -- registers all tables on Base.metadata
from db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    raise RuntimeError(
        "DATABASE_URL is not set. See docs/DB_SETUP.md -- migrations run as "
        "the table-owning role (asc_owner in local dev), not the app role."
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection -- used to sanity
    check migration syntax in environments without a running Postgres."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
