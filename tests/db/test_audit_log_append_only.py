"""audit_log is append-only: asc_app can INSERT and SELECT, but has no
UPDATE or DELETE grant. Proven by attempting both and expecting Postgres to
reject them at the permission level -- inspecting information_schema would
only prove the grant *should* be revoked, not that Postgres is actually
enforcing it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.tenancy import tenant_session


def _new_tenant_with_audit_entry(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory() as session, session.begin():
        tenant = repository.create_tenant(session, f"Audit test tenant {uuid.uuid4()}")
        tenant_id = tenant.id

    with tenant_session(session_factory, tenant_id) as session:
        entry = repository.write_audit_log(
            session,
            tenant_id,
            actor="tester",
            action="read",
            resource_type="claim",
            resource_id="some-claim-id",
        )
        entry_id = entry.id
    return tenant_id, entry_id


def test_audit_log_update_is_rejected(app_session_factory: sessionmaker[Session]) -> None:
    tenant_id, entry_id = _new_tenant_with_audit_entry(app_session_factory)

    with pytest.raises(DBAPIError, match="permission denied"):
        with tenant_session(app_session_factory, tenant_id) as session:
            session.execute(
                text("UPDATE audit_log SET actor = 'tampered' WHERE id = :id"),
                {"id": str(entry_id)},
            )


def test_audit_log_delete_is_rejected(app_session_factory: sessionmaker[Session]) -> None:
    tenant_id, entry_id = _new_tenant_with_audit_entry(app_session_factory)

    with pytest.raises(DBAPIError, match="permission denied"):
        with tenant_session(app_session_factory, tenant_id) as session:
            session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": str(entry_id)})


def test_audit_log_insert_and_select_still_work(
    app_session_factory: sessionmaker[Session],
) -> None:
    """The append-only restriction shouldn't be so broad it breaks the
    actual audit-logging path."""
    tenant_id, entry_id = _new_tenant_with_audit_entry(app_session_factory)

    with tenant_session(app_session_factory, tenant_id) as session:
        row = session.execute(
            text("SELECT actor FROM audit_log WHERE id = :id"), {"id": str(entry_id)}
        ).scalar_one()

    assert row == "tester"
