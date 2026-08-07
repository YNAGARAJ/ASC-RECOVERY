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

Membership creation/revocation (invite, offboard, API-key provisioning)
are separate, later steps -- this is read-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

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
