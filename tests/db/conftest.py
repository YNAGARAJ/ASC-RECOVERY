"""Fixtures for the Phase 3 DB test suite.

Every test in this directory requires a live Postgres 16 with migrations
applied. Without `TEST_DATABASE_URL` set, every test is skipped with an
explicit message -- see docs/DB_SETUP.md -- never silently passed.

`TEST_DATABASE_URL` should point at Postgres as the `asc_app` role (the
same role the application connects as), so the RLS tests are a genuine
proof rather than a superuser/table-owner false pass. `DATABASE_URL`
(optional, falls back to `TEST_DATABASE_URL`) is the table-owning role,
needed only by the RLS test's "prove it's RLS, not missing data" leg.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.base import make_engine, make_session_factory
from security.rbac import Role

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/db/ needs a live Postgres 16 "
            "with migrations applied, connected as the asc_app role. "
            "See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    # Every test in this directory depends on this fixture (directly or via
    # owner_engine's fallback), so checking here is what actually skips the
    # whole suite -- a session-scoped fixture is set up before any
    # function-scoped fixture regardless of autouse or declaration order,
    # so a separate function-scoped guard fixture would run too late.
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    _require_database_url()
    url = OWNER_DATABASE_URL or TEST_DATABASE_URL
    assert url is not None
    return make_engine(url)


def seed_org_facility_user(
    owner_engine: Engine, label: str, *, role: Role = Role.ORG_ADMIN
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Creates a fresh org + facility + user + membership via the
    owner-role connection, not `app_session_factory` -- `organizations`/
    `facilities`/`memberships` are RLS-protected against *resolved*
    access, and there is no membership yet for a brand-new org to
    resolve (asc_app lacks BYPASSRLS; only asc_owner, per
    `alembic/versions/0001_initial_schema.py`'s own docstring, can
    bootstrap this the same way `scripts/onboard_customer.py` does).
    Returns `(user_id, org_id, facility_id)` -- callers pass `user_id`
    to `db.access.access_session` and `facility_id`/`org_id` to whatever
    they're seeding next."""
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        org = repository.create_organization(
            session, parent_org_id=None, type="ASC", name=f"{label} {uuid.uuid4()}"
        )
        facility = repository.create_facility(session, org.id, name=f"{label} facility")
        user = repository.create_user(session, subject=f"{label}-{uuid.uuid4()}@test")
        repository.create_membership(
            session, user.id, org.id, role=role.value, scope="ALL_FACILITIES"
        )
    return user.id, org.id, facility.id
