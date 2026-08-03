"""Tests for session issuance -- the MFA-cannot-be-bypassed proof, refresh
rotation, expiry, and require_recent_auth.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

import security.session as session_module
from security.rbac import Role
from security.session import (
    InvalidTokenError,
    MFANotVerifiedError,
    issue_session,
    refresh_session,
    require_recent_auth,
    validate_access_token,
)

_SECRET = "test-secret-key-at-least-32-bytes-long-for-hs256"


# --- MFA cannot be bypassed --------------------------------------------------


def test_issue_session_rejects_missing_mfa() -> None:
    with pytest.raises(MFANotVerifiedError):
        issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=False)


def test_issue_session_succeeds_once_mfa_is_verified() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True)
    claims = validate_access_token(_SECRET, tokens.access_token)
    assert claims.user_id == "user-1"
    assert claims.role == Role.BILLER


def test_only_issue_session_can_mint_a_token_from_a_bare_role() -> None:
    """The structural proof: every other public function in this module
    only ever operates on a token string that already exists (it takes no
    `role` parameter), so none of them can be used to construct a session
    for an arbitrary user/role and route around the MFA check above. This
    doesn't prove Phase 6's future login endpoint will always pass an
    honest mfa_verified value -- only that, within this module, there is
    no second door."""
    public_functions = [
        (name, obj)
        for name, obj in inspect.getmembers(session_module, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == session_module.__name__
    ]
    assert {name for name, _ in public_functions} == {
        "issue_session",
        "refresh_session",
        "validate_access_token",
        "require_recent_auth",
    }
    functions_accepting_role = [
        name for name, obj in public_functions if "role" in inspect.signature(obj).parameters
    ]
    assert functions_accepting_role == ["issue_session"]


def test_refresh_session_signature_cannot_specify_a_role_or_user() -> None:
    params = set(inspect.signature(refresh_session).parameters)
    assert "role" not in params
    assert "user_id" not in params
    assert "mfa_verified" not in params


# --- Refresh rotation ----------------------------------------------------------


def test_refresh_preserves_the_original_auth_time() -> None:
    """Refreshing never resets 'how long ago was MFA actually verified.'"""
    original_time = datetime.now(UTC) - timedelta(minutes=2)
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True, now=original_time)

    revoked: set[str] = set()
    new_tokens = refresh_session(_SECRET, tokens.refresh_token, revoked_ids=revoked)

    claims = validate_access_token(_SECRET, new_tokens.access_token)
    assert claims.authenticated_at == original_time


def test_refresh_issues_a_new_refresh_token_id() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True)
    new_tokens = refresh_session(_SECRET, tokens.refresh_token, revoked_ids=set())
    assert new_tokens.refresh_token_id != tokens.refresh_token_id


def test_refresh_rejects_a_replayed_refresh_token() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True)
    revoked: set[str] = set()

    refresh_session(_SECRET, tokens.refresh_token, revoked_ids=revoked)
    revoked.add(tokens.refresh_token_id)  # caller's responsibility after a successful refresh

    with pytest.raises(InvalidTokenError):
        refresh_session(_SECRET, tokens.refresh_token, revoked_ids=revoked)


def test_refresh_rejects_an_access_token_presented_as_a_refresh_token() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True)
    with pytest.raises(InvalidTokenError):
        refresh_session(_SECRET, tokens.access_token, revoked_ids=set())


# --- Validation edge cases ------------------------------------------------------


def test_validate_rejects_a_refresh_token_presented_as_an_access_token() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.VIEWER, mfa_verified=True)
    with pytest.raises(InvalidTokenError):
        validate_access_token(_SECRET, tokens.refresh_token)


def test_validate_rejects_the_wrong_secret_key() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.VIEWER, mfa_verified=True)
    with pytest.raises(InvalidTokenError):
        validate_access_token("a-different-secret-also-at-least-32-bytes", tokens.access_token)


def test_validate_rejects_a_garbage_token() -> None:
    with pytest.raises(InvalidTokenError):
        validate_access_token(_SECRET, "not.a.jwt")


def test_access_token_expires_after_its_ttl() -> None:
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    tokens = issue_session(_SECRET, "user-1", Role.VIEWER, mfa_verified=True, now=long_ago)
    with pytest.raises(InvalidTokenError):
        validate_access_token(_SECRET, tokens.access_token)


# --- require_recent_auth --------------------------------------------------------


def test_require_recent_auth_true_when_freshly_authenticated() -> None:
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True)
    claims = validate_access_token(_SECRET, tokens.access_token)
    assert require_recent_auth(claims) is True


def test_require_recent_auth_false_when_stale() -> None:
    old_time = datetime.now(UTC) - timedelta(minutes=10)
    tokens = issue_session(_SECRET, "user-1", Role.BILLER, mfa_verified=True, now=old_time)
    claims = validate_access_token(_SECRET, tokens.access_token)
    assert require_recent_auth(claims) is False
