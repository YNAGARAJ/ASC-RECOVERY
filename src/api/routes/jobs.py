"""`GET /jobs`, `GET /jobs/{id}`, `POST /jobs/{id}/cancel` (Phase 7,
`docs/MASTER-BUILD-PROMPT-V2.md`) -- job history and cooperative
cancellation. Gated by `Action.UPLOAD_REMITTANCE`, the same action
`POST /remittances` already requires -- no new `Action` needed
(`docs/PERMISSIONS.md`): whoever can enqueue ingestion work is exactly
who should be able to see and cancel their own facility's jobs.

Never returns a job's `payload_encrypted` -- `api.repository.JobSummary`
has no field for it at all, so there is no code path here that could
leak it even by accident.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.alerting import record_not_found
from api.auth import AuthContext, get_repository, require_facility, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Page, Repository
from api.schemas import JobListOut, JobOut
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.UPLOAD_REMITTANCE),
    repository: Repository = Depends(get_repository),
) -> JobListOut:
    result = repository.list_jobs(ctx.user_id, require_facility(ctx), page=page)
    return JobListOut.from_domain(result)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(
    job_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.UPLOAD_REMITTANCE),
    repository: Repository = Depends(get_repository),
) -> JobOut:
    job = repository.get_job(ctx.user_id, require_facility(ctx), job_id)
    if job is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobOut.from_domain(job)


@router.post("/jobs/{job_id}/cancel", status_code=204, response_model=None)
def cancel_job(
    job_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.UPLOAD_REMITTANCE),
    repository: Repository = Depends(get_repository),
) -> None:
    cancelled = repository.cancel_job(ctx.user_id, require_facility(ctx), job_id)
    if not cancelled:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
