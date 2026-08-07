"""DB-backed fixtures for tests/jobs/'s live-DB test(s). Same skip-without-
a-live-Postgres pattern as tests/db/conftest.py -- duplicated rather than
shared across sibling test packages, since pytest conftest discovery is
scoped to a directory and its descendants, not siblings. See
tests/db/conftest.py's docstring for why the skip check lives in the
session-scoped fixture itself rather than a separate autouse guard.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.base import make_engine, make_session_factory

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
OWNER_DATABASE_URL = os.environ.get("DATABASE_URL")


def _require_database_url() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip(
            "TEST_DATABASE_URL is not set -- tests/jobs/'s live-DB tests need a "
            "live Postgres 16 with migrations applied, connected as the asc_app "
            "role. See docs/DB_SETUP.md."
        )


@pytest.fixture(scope="session")
def app_session_factory() -> sessionmaker[Session]:
    _require_database_url()
    assert TEST_DATABASE_URL is not None
    return make_session_factory(make_engine(TEST_DATABASE_URL))


@pytest.fixture(scope="session")
def owner_engine() -> Engine:
    _require_database_url()
    url = OWNER_DATABASE_URL or TEST_DATABASE_URL
    assert url is not None
    return make_engine(url)


@pytest.fixture(scope="session")
def owner_session_factory(owner_engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(owner_engine)
