"""Phase 5 step 2's RLS proof: `org_authoring_select`
(`alembic/versions/0008_membership_read_policy.py`) is what lets an
org-resolved caller see *another* user's `memberships` row -- 0007's
self-only SELECT policy alone would return nothing for anyone but the
querying user themselves. Same "RLS, not application code" technique as
`tests/db/test_rls_tenant_isolation.py`, scoped to this one policy pair
(`db.repository.list_org_memberships`, the function
`api.repository.PostgresRepository.list_org_members` wraps).
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


def test_org_admin_sees_other_members_in_their_org(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(
        owner_engine, "Delegated admin read test", role=Role.ORG_ADMIN
    )
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        biller = repository.create_user(session, subject=f"biller-{uuid.uuid4()}@test")
        repository.create_membership(
            session, biller.id, org_id, role=Role.BILLER.value, scope="ALL_FACILITIES"
        )
        biller_subject = biller.subject

    with access_session(app_session_factory, admin_id) as session:
        members, total = repository.list_org_memberships(session, org_id)

    assert total == 2
    assert len(members) == 2
    assert biller_subject in {m.subject for m in members}


def test_caller_outside_org_sees_nothing(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    """Same IDOR-shaped proof as `test_rls_tenant_isolation.py`'s
    cross-facility checks: a caller with no resolved access to `org_a`
    gets an empty result for a known, real `org_id`, never an error and
    never another org's rows."""
    _, org_a, _ = seed_org_facility_user(owner_engine, "Delegated admin read test A")
    outsider_id, _, _ = seed_org_facility_user(owner_engine, "Delegated admin read test B")

    with access_session(app_session_factory, outsider_id) as session:
        members, total = repository.list_org_memberships(session, org_a)

    assert members == []
    assert total == 0
