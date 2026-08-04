"""Request authentication/authorization dependencies.

Resolves a bearer token into a full `AuthContext` -- including `tenant_id`,
which is never accepted from the client (no path/query/body field anywhere
in this API carries one). See `docs/MASTER-BUILD-PROMPT.md` Phase 6 gate:
"no endpoint returns another tenant's data under any parameter
manipulation" is true here by construction, not by a defensive check --
there is no tenant parameter to manipulate.

`validate_access_token` (security.session, Phase 4) proves `(user_id,
role, auth_time)` from the token alone. It carries no tenant_id, so this
module resolves `user_id -> tenant_id` via `Repository.get_user_by_subject`
-- a lookup that is deliberately not tenant-scoped (see db.models.User's
docstring for why that's unavoidable).
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
    user_id: str
    role: Role
    tenant_id: uuid.UUID
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
    if user.role != claims.role.value:
        # The token's role and the current DB-assigned role disagree --
        # the safe default is to trust neither and force re-auth, not to
        # pick one.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="role assignment has changed"
        )

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return AuthContext(
        user_id=claims.user_id, role=claims.role, tenant_id=user.tenant_id, request_id=request_id
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
