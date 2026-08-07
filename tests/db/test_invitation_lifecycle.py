"""Phase 5 step 3's two `SECURITY DEFINER` functions
(`alembic/versions/0007_user_lifecycle.py`): `get_invitation_by_token_hash`
(anonymous read) and `accept_invitation` (anonymous write -- creates the
user + membership + copies invitation_facilities, atomically, then marks
the invitation accepted). Both run with no `app.user_id` set at all,
proving the "token possession is the authorization" bootstrap actually
works against real RLS, not just the API layer's own pre-checks
(`api.repository.PostgresRepository.accept_invitation`'s docstring).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from db import repository
from db.access import access_session
from db.base import make_session_factory
from security.rbac import Role
from security.tokens import generate_token, hash_token
from tests.db.conftest import seed_org_facility_user


def _seed_pending_invitation(
    app_session_factory: sessionmaker[Session],
    admin_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    expires_at: datetime | None = None,
) -> tuple[str, str]:
    """Returns `(raw_token, subject)`."""
    raw_token = generate_token()
    subject = f"invitee-{uuid.uuid4()}@test"
    with access_session(app_session_factory, admin_id) as session:
        repository.create_invitation(
            session,
            org_id,
            subject=subject,
            role=Role.BILLER.value,
            scope="ALL_FACILITIES",
            invited_by=admin_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at or (datetime.now(UTC) + timedelta(days=7)),
        )
    return raw_token, subject


def test_get_invitation_by_token_hash_requires_no_authenticated_session(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Invitation lookup test")
    raw_token, subject = _seed_pending_invitation(app_session_factory, admin_id, org_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session:
        preview = repository.get_invitation_by_token_hash(session, hash_token(raw_token))

    assert preview is not None
    assert preview.subject == subject
    assert preview.status == "pending"

    with session_factory() as session:
        missing = repository.get_invitation_by_token_hash(session, hash_token("bogus-token"))
    assert missing is None


def test_accept_invitation_creates_user_and_membership(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Invitation accept test")
    raw_token, subject = _seed_pending_invitation(app_session_factory, admin_id, org_id)

    session_factory = make_session_factory(owner_engine)
    with session_factory() as session, session.begin():
        result = repository.accept_invitation(
            session,
            hash_token(raw_token),
            password_hash="scrypt$test-not-a-real-hash",
            mfa_secret_encrypted="test-not-real-ciphertext",
        )

    assert result.user_id is not None
    assert result.membership_id is not None

    with access_session(app_session_factory, result.user_id) as session:
        role = repository.resolve_membership_role(session, result.user_id, org_id)
    assert role == Role.BILLER.value


def test_accept_invitation_rejects_a_second_acceptance(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Invitation double-accept test")
    raw_token, _ = _seed_pending_invitation(app_session_factory, admin_id, org_id)
    session_factory = make_session_factory(owner_engine)

    with session_factory() as session, session.begin():
        repository.accept_invitation(
            session,
            hash_token(raw_token),
            password_hash="scrypt$test-not-a-real-hash",
            mfa_secret_encrypted="test-not-real-ciphertext",
        )

    with pytest.raises(DBAPIError):
        with session_factory() as session, session.begin():
            repository.accept_invitation(
                session,
                hash_token(raw_token),
                password_hash="scrypt$test-not-a-real-hash-2",
                mfa_secret_encrypted="test-not-real-ciphertext-2",
            )


def test_accept_invitation_rejects_an_expired_invitation(
    app_session_factory: sessionmaker[Session], owner_engine: Engine
) -> None:
    admin_id, org_id, _ = seed_org_facility_user(owner_engine, "Invitation expiry test")
    raw_token, _ = _seed_pending_invitation(
        app_session_factory,
        admin_id,
        org_id,
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    session_factory = make_session_factory(owner_engine)

    with pytest.raises(DBAPIError):
        with session_factory() as session, session.begin():
            repository.accept_invitation(
                session,
                hash_token(raw_token),
                password_hash="scrypt$test-not-a-real-hash",
                mfa_secret_encrypted="test-not-real-ciphertext",
            )
