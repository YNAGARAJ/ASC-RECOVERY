"""GET /audit-log and GET /claims/{claim_id}/access-history -- both for
the auditor role (and admin), per security/rbac.py's Action.READ_AUDIT_LOG
and Action.READ_PHI_ACCESS_LOG grants."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.auth import AuthContext, get_repository, require_facility, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import AuditLogFilters, Page, Repository
from api.schemas import AccessHistoryOut, AuditLogListOut
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


def _filters(
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> AuditLogFilters:
    return AuditLogFilters(
        actor=actor,
        action=action,
        resource_type=resource_type,
        date_from=date_from,
        date_to=date_to,
    )


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.get("/audit-log", response_model=AuditLogListOut)
def list_audit_log(
    filters: AuditLogFilters = Depends(_filters),
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.READ_AUDIT_LOG),
    repository: Repository = Depends(get_repository),
) -> AuditLogListOut:
    result = repository.list_audit_log(
        ctx.user_id, require_facility(ctx), filters=filters, page=page
    )
    return AuditLogListOut.from_domain(result)


@router.get("/claims/{claim_id}/access-history", response_model=AccessHistoryOut)
def get_claim_access_history(
    claim_id: UUID,
    ctx: AuthContext = require_permission(Action.READ_PHI_ACCESS_LOG),
    repository: Repository = Depends(get_repository),
) -> AccessHistoryOut:
    events = repository.get_claim_access_history(ctx.user_id, require_facility(ctx), claim_id)
    return AccessHistoryOut.from_domain(events)
