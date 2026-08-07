"""POST /remittances -- enqueue an 835 file for ingestion (Phase 7,
`docs/MASTER-BUILD-PROMPT-V2.md`: "nothing heavy may run inside an HTTP
request"). Returns 202 with a job id/status immediately; the actual
parse-and-apply work happens in `src/jobs/runner.py`, out of any HTTP
request. `GET /jobs/{id}` (`api/routes/jobs.py`) is how a caller learns
the eventual outcome -- there is no longer a synchronous response body
carrying `claims_created`/`findings_created`/etc.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from api.auth import AuthContext, get_repository, require_facility, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Repository
from api.schemas import JobOut
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.post("/remittances", response_model=JobOut, status_code=202)
async def upload_remittance(
    file: UploadFile = File(...),
    ctx: AuthContext = require_permission(Action.UPLOAD_REMITTANCE),
    repository: Repository = Depends(get_repository),
) -> JobOut:
    content = await file.read()
    job = repository.enqueue_remittance_ingestion(
        ctx.user_id,
        require_facility(ctx),
        content=content,
        source="upload",
        uploaded_by=ctx.subject,
    )
    return JobOut.from_domain(job)
