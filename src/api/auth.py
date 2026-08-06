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
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from api.repository import Repository
from security.rbac import Action, Role, can
from security.session import InvalidTokenError, validate_access_token


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
    role = repository.resolve_membership_role(user.id, org_id)
    if role is None:
        # No membership at active_org_id (or any ancestor of it) for this
        # user -- revoked, never existed, or the org itself no longer
        # exists. The safe default is to trust nothing and force
        # re-auth/re-selection of an org, not to guess.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="no active membership grants access to this organization",
        )
    facility_id = repository.resolve_default_facility_id(user.id, org_id)

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return AuthContext(
        user_id=user.id,
        subject=user.subject,
        role=role,
        org_id=org_id,
        facility_id=facility_id,
        request_id=request_id,
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
