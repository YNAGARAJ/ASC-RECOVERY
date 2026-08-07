"""`POST /invitations`, `GET /invitations/{token}`, `POST
/invitations/{token}/accept`, `POST /invitations/{token}/confirm-mfa`
(Phase 5 step 3, `docs/MASTER-BUILD-PROMPT-V2.md`). After `accept` +
`confirm-mfa`, the already-built `POST /auth/login` (F-04/F-05,
`docs/audit/REGISTER.md`) just works, unchanged.

The three token-based routes are deliberately unauthenticated -- no
`AuthContext`, and therefore no `enforce_rate_limit` (which itself
requires one via `get_auth_context`; there is no session yet by
definition for an invitee who hasn't logged in). Token possession is the
authorization (`security.tokens`, mirroring a password-reset link), same
principle `accept_invitation`'s own `SECURITY DEFINER` docstring
documents at the database layer
(`alembic/versions/0007_user_lifecycle.py`).

`confirm-mfa` deliberately carries no lockout/rate-limit protection of
its own (unlike `POST /auth/login`'s `AccountLockoutTracker`): it writes
nothing and grants no session -- a successful guess only yields
`{"verified": true}`, which is useless to an attacker who doesn't also
know the password chosen during `accept` (a secret this endpoint never
touches). The actual TOTP-verification gate that matters for access is
`POST /auth/login`, already hardened; adding a second lockout mechanism
here would protect a UX confidence-check that has no exploitable
consequence on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import AuthContext, get_repository, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Repository
from api.schemas import (
    AcceptInvitationIn,
    AcceptInvitationOut,
    ConfirmInvitationMfaIn,
    ConfirmInvitationMfaOut,
    CreateInvitationIn,
    InvitationCreatedOut,
    InvitationPreviewOut,
)
from security.rbac import Action

router = APIRouter()

_INVITATION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="invitation not found or no longer valid"
)


@router.post(
    "/invitations",
    response_model=InvitationCreatedOut,
    status_code=201,
    dependencies=[Depends(enforce_rate_limit)],
)
def create_invitation(
    body: CreateInvitationIn,
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> InvitationCreatedOut:
    if body.scope == "SPECIFIC_FACILITIES" and not body.facility_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="facility_ids is required when scope is SPECIFIC_FACILITIES",
        )
    row = repository.create_invitation(
        ctx.user_id,
        ctx.org_id,
        subject=body.subject,
        role=body.role,
        scope=body.scope,
        facility_ids=body.facility_ids,
    )
    return InvitationCreatedOut.from_domain(row)


@router.get("/invitations/{token}", response_model=InvitationPreviewOut)
def preview_invitation(
    token: str, repository: Repository = Depends(get_repository)
) -> InvitationPreviewOut:
    row = repository.get_invitation_preview(token)
    if row is None or row.status != "pending" or row.expires_at < datetime.now(UTC):
        raise _INVITATION_NOT_FOUND
    return InvitationPreviewOut.from_domain(row)


@router.post("/invitations/{token}/accept", response_model=AcceptInvitationOut)
def accept_invitation(
    token: str,
    body: AcceptInvitationIn,
    repository: Repository = Depends(get_repository),
) -> AcceptInvitationOut:
    result = repository.accept_invitation(token, password=body.password)
    if result is None:
        raise _INVITATION_NOT_FOUND
    return AcceptInvitationOut.from_domain(result)


@router.post("/invitations/{token}/confirm-mfa", response_model=ConfirmInvitationMfaOut)
def confirm_invitation_mfa(
    token: str,
    body: ConfirmInvitationMfaIn,
    repository: Repository = Depends(get_repository),
) -> ConfirmInvitationMfaOut:
    verified = repository.verify_invitation_mfa(token, totp_code=body.totp_code)
    if verified is None:
        raise _INVITATION_NOT_FOUND
    return ConfirmInvitationMfaOut(verified=verified)
