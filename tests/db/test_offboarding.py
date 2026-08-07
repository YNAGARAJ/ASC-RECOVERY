"""Phase 5 step 4's RLS proof: `org_authoring_update`
(`alembic/versions/0007_user_lifecycle.py`) is what lets an org-resolved
caller -- not just the owner-role connection -- soft-revoke *another*
user's `memberships` row via `db.repository.revoke_membership`, the
function `api.repository.PostgresRepository.revoke_membership` wraps.
Phase 4's own `test_revoking_membership_revokes_access_immediately`
(`tests/db/test_rls_tenant_isolation.py`) already proved the
access-resolution side of "revocation takes effect immediately" using a
hard `DELETE` through the owner connection; this proves the actual
production write path -- an `UPDATE` through `asc_app`, gated by RLS --
does the same.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from security.rbac import Role
from tests.db.conftest import seed_org_facility_user


def test_org_admin_can_revoke_another_users_membership(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(
        owner_engine, "Offboarding test", role=Role.ORG_ADMIN
    )
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        biller = repository.create_user(session, subject=f"offboardee-{uuid.uuid4()}@test")
        membership = repository.create_membership(
            session, biller.id, org_id, role=Role.BILLER.value, scope="ALL_FACILITIES"
        )
        biller_id = biller.id
        membership_id = membership.id

    with access_session(app_session_factory, biller_id) as session:
        role_before = repository.resolve_membership_role(session, biller_id, org_id)
    assert role_before == Role.BILLER.value

    with access_session(app_session_factory, admin_id) as session:
        revoked = repository.revoke_membership(session, membership_id)
    assert revoked is True

    with access_session(app_session_factory, biller_id) as session:
        role_after = repository.resolve_membership_role(session, biller_id, org_id)
    assert role_after is None


def test_caller_outside_the_org_cannot_revoke_a_membership(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    _, org_a, _ = seed_org_facility_user(owner_engine, "Offboarding isolation test A")
    outsider_id, _, _ = seed_org_facility_user(owner_engine, "Offboarding isolation test B")
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        target = repository.create_user(session, subject=f"target-{uuid.uuid4()}@test")
        membership = repository.create_membership(
            session, target.id, org_a, role=Role.BILLER.value, scope="ALL_FACILITIES"
        )
        membership_id = membership.id

    with access_session(app_session_factory, outsider_id) as session:
        revoked = repository.revoke_membership(session, membership_id)
    # RLS narrows the UPDATE's WHERE clause to zero matching rows -- same
    # "doesn't exist" vs "not accessible" indistinguishability as every
    # other resolved-access query, not a raised error.
    assert revoked is False

    with access_session(app_session_factory, target.id) as session:
        role_still_active = repository.resolve_membership_role(session, target.id, org_a)
    assert role_still_active == Role.BILLER.value


def test_revoking_an_already_revoked_membership_is_a_no_op(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(
        owner_engine, "Offboarding double-revoke test", role=Role.ORG_ADMIN
    )
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        target = repository.create_user(session, subject=f"target-{uuid.uuid4()}@test")
        membership = repository.create_membership(
            session, target.id, org_id, role=Role.BILLER.value, scope="ALL_FACILITIES"
        )
        membership_id = membership.id

    with access_session(app_session_factory, admin_id) as session:
        first = repository.revoke_membership(session, membership_id)
    assert first is True

    with access_session(app_session_factory, admin_id) as session:
        second = repository.revoke_membership(session, membership_id)
    assert second is False
