"""Phase 5 step 5's RLS proof: `get_api_key_by_hash`
(`alembic/versions/0009_api_key_lookup_function.py`) is what lets an
anonymous caller -- no `app.user_id` set yet -- look up an API key by its
hash, the same bootstrap problem `get_invitation_by_token_hash` solves
for invitation acceptance. `api_keys`' `org_access` policy (0007) is what
lets an org-resolved caller create/revoke a key belonging to their own
resolved org, and denies an outsider the same write -- same shape as
`tests/db/test_offboarding.py` proves for `revoke_membership`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from db.models import ApiKey as ApiKeyModel
from security.rbac import Role
from tests.db.conftest import seed_org_facility_user

_EXPIRES_AT = datetime.now(UTC) + timedelta(days=365)


def _key_hash() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def test_get_api_key_by_hash_is_anonymous_and_bypasses_rls(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Api key lookup test")
    session_factory = make_session_factory(owner_engine)
    key_hash = _key_hash()
    with session_factory() as session, session.begin():
        service_user = repository.create_user(session, subject=f"api-key-{uuid.uuid4()}@test")
        repository.create_membership(
            session, service_user.id, org_id, role=Role.API_SERVICE.value, scope="ALL_FACILITIES"
        )
        api_key = repository.create_api_key(
            session,
            org_id,
            service_user.id,
            name="db test key",
            key_hash=key_hash,
            created_by=admin_id,
            expires_at=_EXPIRES_AT,
        )
        api_key_id, service_user_id = api_key.id, service_user.id

    # A plain, unauthenticated session -- no access_session, no
    # app.user_id -- still resolves the key by hash, proving the
    # SECURITY DEFINER function bypasses RLS the same way
    # get_invitation_by_token_hash does.
    with session_factory() as session:
        found = repository.get_api_key_by_hash(session, key_hash)
    assert found is not None
    assert found.id == api_key_id
    assert found.org_id == org_id
    assert found.user_id == service_user_id
    assert found.revoked_at is None

    with session_factory() as session:
        missing = repository.get_api_key_by_hash(session, _key_hash())
    assert missing is None


def test_org_admin_can_revoke_an_api_key(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(
        owner_engine, "Api key revoke test", role=Role.ORG_ADMIN
    )
    session_factory = make_session_factory(owner_engine)
    key_hash = _key_hash()
    with session_factory() as session, session.begin():
        service_user = repository.create_user(session, subject=f"api-key-{uuid.uuid4()}@test")
        repository.create_membership(
            session, service_user.id, org_id, role=Role.API_SERVICE.value, scope="ALL_FACILITIES"
        )
        api_key = repository.create_api_key(
            session,
            org_id,
            service_user.id,
            name="db revoke key",
            key_hash=key_hash,
            created_by=admin_id,
            expires_at=_EXPIRES_AT,
        )
        api_key_id = api_key.id

    with access_session(app_session_factory, admin_id) as session:
        revoked = repository.revoke_api_key(session, api_key_id)
    assert revoked is True

    with session_factory() as session:
        found = repository.get_api_key_by_hash(session, key_hash)
    assert found is not None
    assert found.revoked_at is not None


def test_revoking_an_already_revoked_api_key_is_a_no_op(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(
        owner_engine, "Api key double-revoke test", role=Role.ORG_ADMIN
    )
    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        service_user = repository.create_user(session, subject=f"api-key-{uuid.uuid4()}@test")
        repository.create_membership(
            session, service_user.id, org_id, role=Role.API_SERVICE.value, scope="ALL_FACILITIES"
        )
        api_key = repository.create_api_key(
            session,
            org_id,
            service_user.id,
            name="db double-revoke key",
            key_hash=_key_hash(),
            created_by=admin_id,
            expires_at=_EXPIRES_AT,
        )
        api_key_id = api_key.id

    with access_session(app_session_factory, admin_id) as session:
        first = repository.revoke_api_key(session, api_key_id)
    assert first is True

    with access_session(app_session_factory, admin_id) as session:
        second = repository.revoke_api_key(session, api_key_id)
    assert second is False


def test_caller_outside_the_org_cannot_revoke_an_api_key(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    _, org_a, _ = seed_org_facility_user(owner_engine, "Api key isolation test A")
    outsider_id, _, _ = seed_org_facility_user(owner_engine, "Api key isolation test B")
    session_factory = make_session_factory(owner_engine)
    key_hash = _key_hash()
    with session_factory() as session, session.begin():
        service_user = repository.create_user(session, subject=f"api-key-{uuid.uuid4()}@test")
        repository.create_membership(
            session, service_user.id, org_a, role=Role.API_SERVICE.value, scope="ALL_FACILITIES"
        )
        api_key = repository.create_api_key(
            session,
            org_a,
            service_user.id,
            name="db isolation key",
            key_hash=key_hash,
            created_by=service_user.id,
            expires_at=_EXPIRES_AT,
        )
        api_key_id = api_key.id

    with access_session(app_session_factory, outsider_id) as session:
        revoked = repository.revoke_api_key(session, api_key_id)
    # RLS narrows the UPDATE's WHERE clause to zero matching rows -- same
    # "doesn't exist" vs "not accessible" indistinguishability as every
    # other resolved-access write in this codebase.
    assert revoked is False

    with session_factory() as session:
        found = repository.get_api_key_by_hash(session, key_hash)
    assert found is not None
    assert found.revoked_at is None


def test_touch_api_key_last_used_via_the_owning_service_users_own_access(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Api key touch test")
    session_factory = make_session_factory(owner_engine)
    key_hash = _key_hash()
    with session_factory() as session, session.begin():
        service_user = repository.create_user(session, subject=f"api-key-{uuid.uuid4()}@test")
        repository.create_membership(
            session, service_user.id, org_id, role=Role.API_SERVICE.value, scope="ALL_FACILITIES"
        )
        api_key = repository.create_api_key(
            session,
            org_id,
            service_user.id,
            name="db touch key",
            key_hash=key_hash,
            created_by=admin_id,
            expires_at=_EXPIRES_AT,
        )
        api_key_id, service_user_id = api_key.id, service_user.id

    # The org_access policy grants UPDATE scoped by the *caller's* own
    # resolved org access -- this proves that includes the service user's
    # own key, via its own ALL_FACILITIES membership on org_id, not just
    # the admin who created it.
    with access_session(app_session_factory, service_user_id) as session:
        repository.touch_api_key_last_used(session, api_key_id)

    # Plain read through the BYPASSRLS owner connection -- last_used_at
    # isn't part of ApiKeyAuthLookup (auth only needs id/org_id/user_id/
    # name/revoked_at/expires_at), so this checks the raw column directly.
    with session_factory() as session:
        row = session.get(ApiKeyModel, api_key_id)
    assert row is not None
    assert row.last_used_at is not None
