"""Request authentication/authorization dependencies.

Resolves a bearer token into a full `AuthContext` -- including `org_id`,
which is never accepted from the client (no path/query/body field anywhere
in this API carries one; it comes from the token's `active_org_id` claim,
itself only ever set by `issue_session`/a Phase 5 "switch org" flow, never
client-supplied). `role` similarly is never trusted from the token: it is
resolved *fresh from `memberships`* on every single request, keyed by
`(user_id, active_org_id)` -- see `security.session`'s module docstring
for why the token deliberately carries no role claim at all. This is what
makes a revoked/changed membership take effect immediately, with no
token-revocation list: if `resolve_membership_role` finds nothing, the
request is unauthenticated, full stop, regardless of how validly-signed
and unexpired the token is.

`user_id -> Membership` resolution needs `db/access.py`'s
`access_session` (RLS-scoped to that user) exactly like any other
resolved-access query -- but `subject -> users.id` itself is deliberately
not scoped (see `db.models.User`'s docstring for why that's unavoidable,
the same bootstrap problem `organizations`/`facilities` don't have).

`subject` (a human-readable identifier -- email or external IdP subject,
`db.models.User.subject`) is carried separately from `user_id` (the
opaque DB primary key `access_session` needs) specifically for audit-log
`actor` fields -- those want something a reviewer can read, not a UUID.

**API keys (Phase 5 step 5)** authenticate the same bearer header, just
never a JWT: `token.startswith(API_KEY_PREFIX)` branches to
`_auth_context_from_api_key` *before* `validate_access_token` is ever
called, so an API key is never run through JWT signature verification (it
isn't one) and a JWT is never hashed and looked up as a key (wasted
work). Both branches converge on the same `_resolve_auth_context` --
role/facility resolution is identical either way, reusing the exact same
`memberships`-backed machinery, since an API key's `user_id` is an
ordinary service user with an ordinary `Membership` row
(`db.models.ApiKey`'s docstring). This is also what makes offboarding a
service account work exactly like offboarding a human one: revoking that
`Membership` (not the `ApiKey` row itself) makes `resolve_membership_role`
return `None` on the very next request either way.

**Per-org IP allowlist (Phase 5 step 6)** is enforced in
`_resolve_auth_context`, after role resolution -- both the JWT and
API-key branches converge here, so a configured `org_policies
.ip_allowlist` restricts either kind of credential identically. A
missing policy row or an empty/unset list means no restriction (the
lazy-creation default, `db.models.OrgPolicy`'s docstring); matching
itself is `security.ip_allowlist.ip_allowed`, a pure function this module
never re-implements.

**Forced re-auth for PHI export (`MASTER-BUILD-PROMPT-V2.md` Phase 6)**:
`AuthContext.authenticated_at` carries when the caller's session actually
completed MFA -- for a JWT, the token's `auth_time` claim (unchanged
across a refresh, `security/session.py`'s own docstring); for an API
key, `datetime.now(UTC)` at the moment it authenticated, since presenting
a raw secret each time is itself a fresh authentication with no
"session age" concept the way a JWT has. `require_permission_with_recent_auth`
additionally rejects a request whose `authenticated_at` is older than
`security.session.REAUTH_MAX_AGE`, even when the token itself is still
validly signed and unexpired -- a long-lived-but-not-yet-expired token is
not sufficient proof of a recent human at the keyboard for an action
shaped like bulk PHI export. There is no separate step-up endpoint: the
already-existing `POST /auth/login` mints a fresh `auth_time` simply by
succeeding again, which is the intended remediation for a caller that
fails this check.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status

from api.repository import Repository
from security.ip_allowlist import ip_allowed
from security.rbac import Action, Role, can
from security.session import InvalidTokenError, require_recent_auth, validate_access_token
from security.tokens import API_KEY_PREFIX


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: uuid.UUID
    subject: str
    role: Role
    org_id: uuid.UUID
    # None when the active org resolves to zero or more than one facility
    # -- a real facility switcher is Phase 5/12 scope (see
    # db.repository.get_default_facility_id_for_org's docstring). Routes
    # that need a specific facility target must treat None as "ambiguous,
    # can't proceed" (400), not silently pick one.
    facility_id: uuid.UUID | None
    request_id: str
    # When this session actually completed MFA (JWT) or was presented
    # (API key) -- see this module's docstring on forced re-auth for PHI
    # export. Deliberately not "when the current access token was minted"
    # (a refresh doesn't change it for a JWT; an API key has no separate
    # concept of either).
    authenticated_at: datetime


def get_repository(request: Request) -> Repository:
    repository: Repository = request.app.state.repository
    return repository


def _secret_key(request: Request) -> str:
    key: str = request.app.state.jwt_secret_key
    return key


async def get_auth_context(
    request: Request,
    authorization: str | None = Header(default=None),
    repository: Repository = Depends(get_repository),
) -> AuthContext:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )
    token = authorization.removeprefix("Bearer ")

    if token.startswith(API_KEY_PREFIX):
        return _auth_context_from_api_key(request, repository, token)

    try:
        claims = validate_access_token(_secret_key(request), token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token"
        ) from exc

    user = repository.get_user_by_subject(claims.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")

    org_id = uuid.UUID(claims.active_org_id)
    return _resolve_auth_context(
        request,
        repository,
        user_id=user.id,
        subject=user.subject,
        org_id=org_id,
        authenticated_at=claims.authenticated_at,
    )


def _auth_context_from_api_key(
    request: Request, repository: Repository, token: str
) -> AuthContext:
    record = repository.get_api_key_for_auth(token)
    if record is None or record.revoked_at is not None or record.expires_at < datetime.now(UTC):
        # Unknown, revoked, and expired all collapse to the same response
        # -- an attacker probing a dead key learns nothing about which of
        # the three it was.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired API key"
        )
    ctx = _resolve_auth_context(
        request,
        repository,
        user_id=record.user_id,
        subject=f"api-key:{record.name}",
        org_id=record.org_id,
        authenticated_at=datetime.now(UTC),
    )
    # Best-effort bookkeeping, after the fact -- never blocks the request
    # this key just legitimately authenticated.
    repository.touch_api_key_last_used(record.user_id, record.id)
    return ctx


def _resolve_auth_context(
    request: Request,
    repository: Repository,
    *,
    user_id: uuid.UUID,
    subject: str,
    org_id: uuid.UUID,
    authenticated_at: datetime,
) -> AuthContext:
    role = repository.resolve_membership_role(user_id, org_id)
    if role is None:
        # No membership at org_id (or any ancestor of it) for this user --
        # revoked, never existed, or the org itself no longer exists. The
        # safe default is to trust nothing and force re-auth/re-selection
        # of an org, not to guess.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no active membership grants access to this organization",
        )

    policy = repository.get_org_policy(user_id, org_id)
    if policy is not None and policy.ip_allowlist:
        client_ip = request.client.host if request.client is not None else None
        if not ip_allowed(client_ip, policy.ip_allowlist):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="request origin not permitted by organization policy",
            )

    facility_id = repository.resolve_default_facility_id(user_id, org_id)

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return AuthContext(
        user_id=user_id,
        subject=subject,
        role=role,
        org_id=org_id,
        facility_id=facility_id,
        request_id=request_id,
        authenticated_at=authenticated_at,
    )


def require_permission(action: Action) -> AuthContext:
    """Returns a FastAPI dependency marker, typed as `AuthContext` (the
    type it resolves to once injected) so route signatures like
    `ctx: AuthContext = require_permission(Action.READ_FINDING)` type-check
    -- the same convention `Depends(...)` itself relies on."""

    def _dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not can(ctx.role, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {ctx.role.value!r} cannot perform this action",
            )
        return ctx

    return Depends(_dependency)  # type: ignore[no-any-return]


def require_permission_with_recent_auth(action: Action) -> AuthContext:
    """Same as `require_permission`, plus `require_recent_auth` -- for
    actions shaped like bulk PHI export, where a long-lived-but-unexpired
    token isn't sufficient proof of a recent human at the keyboard (this
    module's own docstring). `POST /auth/login` succeeding again is the
    remediation; there is no separate step-up endpoint."""

    def _dependency(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not can(ctx.role, action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {ctx.role.value!r} cannot perform this action",
            )
        if not require_recent_auth(ctx.authenticated_at):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="recent re-authentication required for this action",
            )
        return ctx

    return Depends(_dependency)  # type: ignore[no-any-return]


def require_facility(ctx: AuthContext) -> uuid.UUID:
    """For routes whose target is a single facility (uploads, finding
    reads, recovery packets, audit log) -- `ctx.facility_id` is `None`
    when the active org resolves to zero or more than one facility (see
    `AuthContext`'s own docstring), which these routes cannot proceed
    against without a real facility switcher (Phase 5/12) to disambiguate.
    Never guesses; always 400s instead."""
    if ctx.facility_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="active organization has no single resolvable facility -- select one first",
        )
    return ctx.facility_id
