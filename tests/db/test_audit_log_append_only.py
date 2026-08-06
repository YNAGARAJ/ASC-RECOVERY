"""audit_log is append-only: asc_app can INSERT and SELECT, but has no
UPDATE or DELETE grant. Proven by attempting both and expecting Postgres to
reject them at the permission level -- inspecting information_schema would
only prove the grant *should* be revoked, not that Postgres is actually
enforcing it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from tests.db.conftest import seed_org_facility_user


def _new_facility_with_audit_entry(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> tuple[uuid.UUID, uuid.UUID]:
    user_id, _org_id, facility_id = seed_org_facility_user(owner_engine, "Audit test tenant")

    with access_session(app_session_factory, user_id) as session:
        entry = repository.write_audit_log(
            session,
            facility_id,
            actor="tester",
            action="read",
            resource_type="claim",
            resource_id="some-claim-id",
        )
        entry_id = entry.id
    return user_id, entry_id


def test_audit_log_update_is_rejected(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, entry_id = _new_facility_with_audit_entry(owner_engine, app_session_factory)

    with pytest.raises(DBAPIError, match="permission denied"):
        with access_session(app_session_factory, user_id) as session:
            session.execute(
                text("UPDATE audit_log SET actor = 'tampered' WHERE id = :id"),
                {"id": str(entry_id)},
            )


def test_audit_log_delete_is_rejected(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    user_id, entry_id = _new_facility_with_audit_entry(owner_engine, app_session_factory)

    with pytest.raises(DBAPIError, match="permission denied"):
        with access_session(app_session_factory, user_id) as session:
            session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": str(entry_id)})


def test_audit_log_insert_and_select_still_work(
    owner_engine: Engine, app_session_factory: sessionmaker[Session]
) -> None:
    """The append-only restriction shouldn't be so broad it breaks the
    actual audit-logging path."""
    user_id, entry_id = _new_facility_with_audit_entry(owner_engine, app_session_factory)

    with access_session(app_session_factory, user_id) as session:
        row = session.execute(
            text("SELECT actor FROM audit_log WHERE id = :id"), {"id": str(entry_id)}
        ).scalar_one()

    assert row == "tester"
