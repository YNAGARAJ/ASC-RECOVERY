"""POST /remittances -- upload an 835 file for ingestion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from api.auth import AuthContext, get_repository, require_facility, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import Repository
from api.schemas import IngestionOutcomeOut
from ingestion.pipeline import DuplicateOutcome
from ingestion.virus_scan import EicarAwareScanner
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])

_scanner = EicarAwareScanner()


@router.post("/remittances", response_model=IngestionOutcomeOut, status_code=201)
async def upload_remittance(
    file: UploadFile = File(...),
    ctx: AuthContext = require_permission(Action.UPLOAD_REMITTANCE),
    repository: Repository = Depends(get_repository),
) -> IngestionOutcomeOut:
    content = await file.read()
    outcome = repository.ingest_remittance(
        ctx.user_id,
        require_facility(ctx),
        content=content,
        source="upload",
        uploaded_by=ctx.subject,
        scanner=_scanner,
    )
    if isinstance(outcome, DuplicateOutcome):
        return IngestionOutcomeOut(
            remittance_id=outcome.remittance_id,
            status="duplicate",
            claims_created=0,
            findings_created=0,
            reconciliation_mismatches=0,
        )
    return IngestionOutcomeOut(
        remittance_id=outcome.remittance_id,
        status=outcome.status,
        claims_created=outcome.claims_created,
        findings_created=outcome.findings_created,
        reconciliation_mismatches=outcome.reconciliation_mismatches,
    )
