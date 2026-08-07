"""GET /findings (filtered + paginated), GET /findings/{finding_id} (full
evidence chain), GET /findings/export.csv (worklist export).

Filters are UUIDs/enums/dates/dollar thresholds only -- never a patient
identifier -- and they're query parameters, so keeping PHI out of them
matters: query strings land in access logs (CLAUDE.md rule 6).

`export.csv` is gated by `require_permission_with_recent_auth`, not
plain `require_permission` (`MASTER-BUILD-PROMPT-V2.md` Phase 6's
"forced re-auth for PHI export" -- `api/auth.py`'s own docstring has the
full mechanism). Applied here as the one route in this API actually
shaped like a bulk export, even though today's CSV columns happen to
carry no patient-identifying fields (see `api.repository.FindingSummary`)
-- the control guards the export *pattern* (a bulk pull that's easy to
exfiltrate from an already-compromised-but-unexpired token), not today's
specific column list, which could grow to include patient identifiers
later without anyone remembering to add this check retroactively.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.alerting import record_not_found
from api.auth import (
    AuthContext,
    get_repository,
    require_facility,
    require_permission,
    require_permission_with_recent_auth,
)
from api.rate_limit import enforce_rate_limit
from api.repository import FindingFilters, Page, RecordOutcomeInput, Repository
from api.schemas import FindingDetailOut, FindingListOut, FindingSummaryOut, RecordOutcomeIn
from domain.outcomes import NothingToAppealError, OutcomeAlreadyRecordedError
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])

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
    result = repository.list_findings(
        ctx.user_id, require_facility(ctx), filters=filters, page=page
    )
    return FindingListOut.from_domain(result)


@router.get("/findings/export.csv")
def export_findings_csv(
    filters: FindingFilters = Depends(_filters),
    ctx: AuthContext = require_permission_with_recent_auth(Action.EXPORT_WORKLIST),
    repository: Repository = Depends(get_repository),
) -> StreamingResponse:
    result = repository.list_findings(
        ctx.user_id,
        require_facility(ctx),
        filters=filters,
        page=Page(limit=_EXPORT_ROW_LIMIT, offset=0),
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
            "outcome",
            "amount_recovered",
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
                item.outcome or "",
                item.amount_recovered or "",
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
    request: Request,
    ctx: AuthContext = require_permission(Action.READ_FINDING),
    repository: Repository = Depends(get_repository),
) -> FindingDetailOut:
    detail = repository.get_finding_detail(
        ctx.user_id, require_facility(ctx), finding_id, actor=ctx.subject, role=ctx.role
    )
    if detail is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
    return FindingDetailOut.from_domain(detail)


@router.post("/findings/{finding_id}/outcome", response_model=FindingSummaryOut)
def record_outcome(
    finding_id: UUID,
    body: RecordOutcomeIn,
    request: Request,
    ctx: AuthContext = require_permission(Action.RECORD_FINDING_OUTCOME),
    repository: Repository = Depends(get_repository),
) -> FindingSummaryOut:
    data = RecordOutcomeInput(
        outcome=body.outcome,
        amount_recovered=None if body.amount_recovered is None else Decimal(body.amount_recovered),
    )
    try:
        row = repository.record_finding_outcome(
            ctx.user_id, require_facility(ctx), finding_id, data=data, recorded_by=ctx.subject
        )
    except OutcomeAlreadyRecordedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NothingToAppealError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if row is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
    return FindingSummaryOut.from_domain(row)
