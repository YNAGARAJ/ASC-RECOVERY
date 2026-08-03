"""Session and token issuance.

MFA-mandatory, no exceptions, no internal-user bypass: `issue_session()` is
the only function in this module that can mint a session from a bare
`(user_id, role)` pair, and it refuses unless `mfa_verified=True`. Every
other function here (`refresh_session`, `validate_access_token`) only ever
operates on a token string that already exists -- it cannot construct a new
session for an arbitrary user/role, so it cannot be used to route around
the MFA check. `refresh_session` carries the original `auth_time` forward
rather than minting a fresh one, so refreshing a session never resets "how
long ago was MFA actually verified."

This module owns what happens *after* a caller has already completed
username/password and MFA verification -- Phase 6's login endpoint is
expected to call `issue_session` once both succeed. It does not implement
the OIDC handshake itself (there's no FastAPI app yet to host it).

Access tokens are short-lived (`ACCESS_TOKEN_TTL`). Refresh tokens rotate
on every use: the caller must track used refresh-token ids (`revoked_ids`)
so a stolen-then-replayed refresh token is rejected.
`require_recent_auth()` is for callers that must force re-auth for a
sensitive action (e.g. a PHI export) even though the access token is still
validly signed and unexpired.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from security.rbac import Role

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)
REAUTH_MAX_AGE = timedelta(minutes=5)

# secret_key must be >= 32 bytes (RFC 7518 section 3.2) -- pull it from
# security.secrets.SecretStore, never hardcode or commit it.
_ALGORITHM = "HS256"


class MFANotVerifiedError(Exception):
    """Raised by issue_session() when mfa_verified is not True."""


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    refresh_token_id: str


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: str
    role: Role
    authenticated_at: datetime


def issue_session(
    secret_key: str,
    user_id: str,
    role: Role,
    *,
    mfa_verified: bool,
    now: datetime | None = None,
) -> SessionTokens:
    if not mfa_verified:
        raise MFANotVerifiedError(
            "cannot issue a session without a verified MFA code -- MFA is mandatory, no exceptions"
        )
    issued_at = now or datetime.now(UTC)
    return _mint_pair(
        secret_key, user_id=user_id, role=role.value, auth_time=issued_at, issued_at=issued_at
    )


def validate_access_token(secret_key: str, token: str) -> AccessTokenClaims:
    payload = _decode(secret_key, token)
    if payload.get("type") != "access":
        raise InvalidTokenError("not an access token")
    return AccessTokenClaims(
        user_id=payload["sub"],
        role=Role(payload["role"]),
        authenticated_at=datetime.fromtimestamp(payload["auth_time"], tz=UTC),
    )


def refresh_session(
    secret_key: str,
    refresh_token: str,
    *,
    revoked_ids: set[str],
    now: datetime | None = None,
) -> SessionTokens:
    """Rotates a refresh token. Validates the presented one, rejects it if
    its id is already in `revoked_ids` (a replay), mints a brand-new
    access+refresh pair carrying the *original* auth_time forward, and the
    caller must add the just-used refresh token's id to `revoked_ids`
    afterward so it can never be presented again."""
    payload = _decode(secret_key, refresh_token)
    if payload.get("type") != "refresh":
        raise InvalidTokenError("not a refresh token")
    token_id = payload["jti"]
    if token_id in revoked_ids:
        raise InvalidTokenError("refresh token has already been used (possible replay)")

    issued_at = now or datetime.now(UTC)
    original_auth_time = datetime.fromtimestamp(payload["auth_time"], tz=UTC)
    return _mint_pair(
        secret_key,
        user_id=payload["sub"],
        role=payload["role"],
        auth_time=original_auth_time,
        issued_at=issued_at,
    )


def require_recent_auth(claims: AccessTokenClaims, *, now: datetime | None = None) -> bool:
    """Callers guarding a sensitive action (e.g. a PHI export) should call
    this and force a fresh login if it returns False, even when the access
    token itself is still validly signed and unexpired."""
    current = now or datetime.now(UTC)
    return (current - claims.authenticated_at) <= REAUTH_MAX_AGE


def _mint_pair(
    secret_key: str, *, user_id: str, role: str, auth_time: datetime, issued_at: datetime
) -> SessionTokens:
    refresh_token_id = str(uuid.uuid4())
    access_payload = {
        "sub": user_id,
        "role": role,
        "auth_time": auth_time.timestamp(),
        "iat": issued_at.timestamp(),
        "exp": (issued_at + ACCESS_TOKEN_TTL).timestamp(),
        "type": "access",
    }
    refresh_payload = {
        "sub": user_id,
        "role": role,
        "auth_time": auth_time.timestamp(),
        "jti": refresh_token_id,
        "iat": issued_at.timestamp(),
        "exp": (issued_at + REFRESH_TOKEN_TTL).timestamp(),
        "type": "refresh",
    }
    access_token = jwt.encode(access_payload, secret_key, algorithm=_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, secret_key, algorithm=_ALGORITHM)
    return SessionTokens(
        access_token=access_token, refresh_token=refresh_token, refresh_token_id=refresh_token_id
    )


def _decode(secret_key: str, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
