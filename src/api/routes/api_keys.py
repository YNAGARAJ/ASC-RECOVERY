"""`POST /api-keys`, `GET /api-keys`, `POST /api-keys/{id}/revoke` (Phase 5
step 5, `docs/MASTER-BUILD-PROMPT-V2.md`). Same delegated-admin
(`Action.MANAGE_USERS`) gating as `api/routes/organizations.py`'s
membership routes -- an org admin provisions machine-to-machine
credentials for their own resolved org, never anyone else's, same
`ctx.org_id`-not-a-client-param discipline as everywhere else in this API.

The actual bearer-token branch that lets a presented API key authenticate
a request at all lives in `api/auth.py::get_auth_context`, not here --
this module only covers the human-driven lifecycle (create/list/revoke).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.alerting import record_not_found
from api.auth import AuthContext, get_repository, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Page, Repository
from api.schemas import ApiKeyCreatedOut, ApiKeyListOut, CreateApiKeyIn
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
def create_api_key(
    body: CreateApiKeyIn,
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> ApiKeyCreatedOut:
    if body.scope == "SPECIFIC_FACILITIES" and not body.facility_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="facility_ids is required when scope is SPECIFIC_FACILITIES",
        )
    row = repository.create_api_key(
        ctx.user_id,
        ctx.org_id,
        name=body.name,
        scope=body.scope,
        facility_ids=body.facility_ids,
    )
    return ApiKeyCreatedOut.from_domain(row)


@router.get("/api-keys", response_model=ApiKeyListOut)
def list_api_keys(
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> ApiKeyListOut:
    result = repository.list_api_keys(ctx.user_id, ctx.org_id, page=page)
    return ApiKeyListOut.from_domain(result)


@router.post("/api-keys/{api_key_id}/revoke", status_code=204, response_model=None)
def revoke_api_key(
    api_key_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.MANAGE_USERS),
    repository: Repository = Depends(get_repository),
) -> None:
    revoked = repository.revoke_api_key(ctx.user_id, api_key_id)
    if not revoked:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api key not found")
