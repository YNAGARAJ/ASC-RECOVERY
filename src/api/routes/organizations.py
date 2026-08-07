"""GET /organizations/members -- delegated admin's read surface (Phase 5
step 2, `docs/MASTER-BUILD-PROMPT-V2.md`). Gated by `Action.MANAGE_USERS`.

No `org_id` path/query param, deliberately -- every other route in this
API resolves its scoping org/facility id server-side from `AuthContext`,
never from the client (`test_tenant_param_absence.py` guards this as a
structural invariant); this lists members of `ctx.org_id`, the caller's
currently active org, the same way `list_contracts` uses `ctx.org_id`
rather than accepting one. Widening this to "every org I can reach in my
resolved tree, not just my active one" is the same deliberate, documented
scope limit `api.repository.Repository`'s docstring already calls out for
`list_contracts`/`list_findings` -- one org/facility per call; an admin
who wants a different org's members switches their active org first
(org-switching itself is a known, not-yet-built gap, same as facility
switching -- `AuthContext.facility_id`'s docstring).

`POST /organizations/members/{membership_id}/revoke` (Phase 5 step 4) is
offboarding -- the phase's actual stated gate ("offboarding test proves
instant session death"). It soft-revokes (`memberships.revoked_at`, not a
`DELETE` -- `asc_app` has no `DELETE` grant on any mutable table); the
next request against *any* route, using an already-issued, unexpired
token, fails immediately -- there is no token-revocation list, because
role/access is resolved fresh from `memberships` on every request
(`api/auth.py::get_auth_context`). `membership_id` is a specific resource
id, like `finding_id`/`packet_id` elsewhere -- not a bare scoping id like
`org_id`/`facility_id` (see this module's own note above on why those
never appear as client-supplied params); RLS's `org_authoring_update`
policy (`alembic/versions/0007_user_lifecycle.py`) is what actually
restricts *which* membership rows a given caller can revoke, so a
cross-org `membership_id` 404s exactly like a nonexistent one would, same
IDOR-shaped guarantee every other direct-id-lookup route in this API
gives.

No dedicated `audit_log` entry for a revocation -- confirmed with the
user: every existing `write_audit_log` call in this codebase is for a
write to a PHI-bearing table (CLAUDE.md rule 5's literal scope);
`memberships` isn't one, and `audit_log.facility_id` is `NOT NULL` with
no clean single-facility target for an `ALL_FACILITIES`-scoped
membership anyway. The gate's actual proof is the instant-session-death
test, not an audit row.

Invitation provisioning lives in its own module (`api/routes/invitations.py`);
API-key provisioning in `api/routes/api_keys.py` (Phase 5 step 5) -- both
the same delegated-admin (`Action.MANAGE_USERS`) pattern as everything
above.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.alerting import record_not_found
from api.auth import AuthContext, get_repository, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Page, Repository
from api.schemas import OrgMemberListOut
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.get("/organizations/members", response_model=OrgMemberListOut)
def list_org_members(
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> OrgMemberListOut:
    result = repository.list_org_members(ctx.user_id, ctx.org_id, page=page)
    return OrgMemberListOut.from_domain(result)


@router.post("/organizations/members/{membership_id}/revoke", status_code=204, response_model=None)
def revoke_membership(
    membership_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> None:
    revoked = repository.revoke_membership(ctx.user_id, membership_id)
    if not revoked:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="membership not found")
