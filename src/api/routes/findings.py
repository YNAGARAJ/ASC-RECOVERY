"""GET /findings (filtered + paginated), GET /findings/{finding_id} (full
evidence chain), GET /findings/export.csv (worklist export).

Filters are UUIDs/enums/dates/dollar thresholds only -- never a patient
identifier -- and they're query parameters, so keeping PHI out of them
matters: query strings land in access logs (CLAUDE.md rule 6).
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from api.auth import AuthContext, get_repository, require_permission
from api.repository import FindingFilters, Page, Repository
from api.schemas import FindingDetailOut, FindingListOut
from security.rbac import Action

router = APIRouter()

_EXPORT_ROW_LIMIT = 10_000


def _filters(
    root_cause: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    min_shortfall: Decimal | None = Query(default=None),
    remittance_id: UUID | None = Query(default=None),
    claim_id: UUID | None = Query(default=None),
) -> FindingFilters:
    return FindingFilters(
        root_cause=root_cause,
        date_from=date_from,
        date_to=date_to,
        min_shortfall=min_shortfall,
        remittance_id=remittance_id,
        claim_id=claim_id,
    )


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.get("/findings", response_model=FindingListOut)
def list_findings(
    filters: FindingFilters = Depends(_filters),
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.READ_FINDING),
    repository: Repository = Depends(get_repository),
) -> FindingListOut:
    result = repository.list_findings(ctx.tenant_id, filters=filters, page=page)
    return FindingListOut.from_domain(result)


@router.get("/findings/export.csv")
def export_findings_csv(
    filters: FindingFilters = Depends(_filters),
    ctx: AuthContext = require_permission(Action.EXPORT_WORKLIST),
    repository: Repository = Depends(get_repository),
) -> StreamingResponse:
    result = repository.list_findings(
        ctx.tenant_id, filters=filters, page=Page(limit=_EXPORT_ROW_LIMIT, offset=0)
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "finding_id",
            "claim_id",
            "procedure_code",
            "expected_allowed",
            "actual_allowed",
            "shortfall",
            "root_cause",
        ]
    )
    for item in result.items:
        writer.writerow(
            [
                str(item.id),
                str(item.claim_id),
                item.procedure_code,
                item.expected_allowed or "",
                item.actual_allowed,
                item.shortfall,
                item.root_cause,
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=worklist.csv"},
    )


@router.get("/findings/{finding_id}", response_model=FindingDetailOut)
def get_finding(
    finding_id: UUID,
    ctx: AuthContext = require_permission(Action.READ_FINDING),
    repository: Repository = Depends(get_repository),
) -> FindingDetailOut:
    detail = repository.get_finding_detail(ctx.tenant_id, finding_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
    return FindingDetailOut.from_domain(detail)
