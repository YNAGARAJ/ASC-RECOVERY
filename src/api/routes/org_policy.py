"""`GET /org-policy`, `PUT /org-policy` (Phase 5 step 6 + Phase 6's
per-org data residency, `docs/MASTER-BUILD-PROMPT-V2.md`). Same
delegated-admin (`Action.MANAGE_USERS`) gating as everything else in
this phase; `PUT` is a full replace (no `PATCH`) since there are only
three settable fields.

`mfa_required` never appears in `UpdateOrgPolicyIn` -- by explicit product
decision (`db.models.OrgPolicy`'s docstring) there is no request shape
that can ever set it to anything but its `true` default. This isn't
enforced by rejecting a client-sent `false`; it's enforced by the field
not existing in the schema at all, so there is no code path to audit for
that mistake.

`data_residency_region` is a stored declaration, not a technical control
(`db.models.OrgPolicy`'s docstring) -- unlike per-org encryption keys
(`organizations.kms_key_id`), it's deliberately self-service here:
misdeclaring it doesn't lock an org out of its own data the way a wrong
KMS key would, so it doesn't need the same operator-only, direct-SQL
treatment.

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
    session_timeout_seconds=None,
    mfa_required=True,
    ip_allowlist=(),
    data_residency_region=None,
    updated_at=None,
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
        data_residency_region=body.data_residency_region,
    )
    return OrgPolicyOut.from_domain(row)
