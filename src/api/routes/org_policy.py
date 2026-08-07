"""`GET /org-policy`, `PUT /org-policy` (Phase 5 step 6,
`docs/MASTER-BUILD-PROMPT-V2.md`). Same delegated-admin
(`Action.MANAGE_USERS`) gating as everything else in this phase; `PUT` is
a full replace (no `PATCH`) since there are only two settable fields.

`mfa_required` never appears in `UpdateOrgPolicyIn` -- by explicit product
decision (`db.models.OrgPolicy`'s docstring) there is no request shape
that can ever set it to anything but its `true` default. This isn't
enforced by rejecting a client-sent `false`; it's enforced by the field
not existing in the schema at all, so there is no code path to audit for
that mistake.

A missing policy row (nothing configured yet) is not a 404 here -- it's
the documented lazy-creation default state (`db.models.OrgPolicy`'s
docstring), so `GET` returns it as a normal 200 with `updated_at: null`
rather than making the caller special-case "not found" for something
that isn't actually missing, just unconfigured.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import AuthContext, get_repository, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import OrgPolicySummary, Repository
from api.schemas import OrgPolicyOut, UpdateOrgPolicyIn
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])

_DEFAULT_POLICY = OrgPolicySummary(
    session_timeout_seconds=None, mfa_required=True, ip_allowlist=(), updated_at=None
)


@router.get("/org-policy", response_model=OrgPolicyOut)
def get_org_policy(
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> OrgPolicyOut:
    row = repository.get_org_policy(ctx.user_id, ctx.org_id)
    return OrgPolicyOut.from_domain(row if row is not None else _DEFAULT_POLICY)


@router.put("/org-policy", response_model=OrgPolicyOut)
def set_org_policy(
    body: UpdateOrgPolicyIn,
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> OrgPolicyOut:
    row = repository.set_org_policy(
        ctx.user_id,
        ctx.org_id,
        session_timeout_seconds=body.session_timeout_seconds,
        ip_allowlist=body.ip_allowlist,
    )
    return OrgPolicyOut.from_domain(row)
