"""Session and token issuance.

MFA-mandatory, no exceptions, no internal-user bypass: `issue_session()` is
the only function in this module that can mint a session from a bare
`(user_id, active_org_id)` pair, and it refuses unless `mfa_verified=True`.
Every other function here (`refresh_session`, `validate_access_token`) only
ever operates on a token string that already exists -- it cannot construct
a new session for an arbitrary user, so it cannot be used to route around
the MFA check. `refresh_session` carries the original `auth_time` forward
rather than minting a fresh one, so refreshing a session never resets "how
long ago was MFA actually verified."

**No `role` claim (Phase 4, `docs/MASTER-BUILD-PROMPT-V2.md`).** A user's
role is per-`Membership` (per org), not a single global value, so it
cannot be baked into the token the way it was under the old flat-tenant
model -- `api/auth.py`'s `get_auth_context` resolves role fresh from
`memberships` on every request, keyed by `(user_id, active_org_id)`. This
is what makes "revoking a membership revokes access immediately" true
with no token-revocation list: the token only proves *who* and *which org
context*, never *what they're allowed to do there*. `active_org_id`
*is* carried in the token -- switching it is a deliberate action (a
Phase 5 "switch org" flow that re-mints a token after verifying the new
membership exists), not something a plain refresh does; `refresh_session`
therefore carries the original `active_org_id` forward unchanged, exactly
like `auth_time`.

This module owns what happens *after* a caller has already completed
username/password and MFA verification -- the login endpoint
(`api/routes/auth.py`) is expected to call `issue_session` once both
succeed. It does not implement the OIDC handshake itself (there's no
FastAPI app yet to host it).

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
    active_org_id: str
    authenticated_at: datetime


def issue_session(
    secret_key: str,
    user_id: str,
    active_org_id: str,
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
        secret_key,
        user_id=user_id,
        active_org_id=active_org_id,
        auth_time=issued_at,
        issued_at=issued_at,
    )


def validate_access_token(secret_key: str, token: str) -> AccessTokenClaims:
    payload = _decode(secret_key, token)
    if payload.get("type") != "access":
        raise InvalidTokenError("not an access token")
    return AccessTokenClaims(
        user_id=payload["sub"],
        active_org_id=payload["active_org_id"],
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
        active_org_id=payload["active_org_id"],
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
    secret_key: str, *, user_id: str, active_org_id: str, auth_time: datetime, issued_at: datetime
) -> SessionTokens:
    refresh_token_id = str(uuid.uuid4())
    access_payload = {
        "sub": user_id,
        "active_org_id": active_org_id,
        "auth_time": auth_time.timestamp(),
        "iat": issued_at.timestamp(),
        "exp": (issued_at + ACCESS_TOKEN_TTL).timestamp(),
        "type": "access",
    }
    refresh_payload = {
        "sub": user_id,
        "active_org_id": active_org_id,
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
