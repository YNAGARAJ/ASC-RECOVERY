"""SQLAlchemy declarative base and engine/session factory."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    # F-07 (docs/audit/REGISTER.md): defends against a DATABASE_URL that
    # forgot to ask for TLS -- the exact real gap B-43 documents for
    # Azure's current secret. If the URL doesn't already specify
    # `sslmode`, default to `require` rather than psycopg's own default
    # of `prefer` (silently falls back to plaintext if the server
    # doesn't offer TLS). An explicit `sslmode` already in the URL is
    # always respected as-is -- including `sslmode=disable`, which local
    # dev and CI's plain (non-TLS) Postgres containers set explicitly for
    # exactly this reason (see docker-compose.yml, .github/workflows/ci.yml).
    url = make_url(database_url)
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
