"""POST /findings/{finding_id}/packets (generate a draft), GET
/findings/{finding_id}/packets (list), POST /packets/{packet_id}/approve,
POST /packets/{packet_id}/reject -- the human-approval step. Approval is
never automatic: `generate_packet` only ever produces `status="draft"`
rows; only a human hitting approve/reject moves a packet out of that
state (CLAUDE.md / Phase 7 prompt).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from api.alerting import record_not_found
from api.auth import AuthContext, get_repository, require_facility, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import PacketGenerationFailed, Repository
from api.schemas import PacketGenerationFailedOut, PacketListOut, RecoveryPacketOut
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


@router.post(
    "/findings/{finding_id}/packets",
    response_model=RecoveryPacketOut,
    status_code=201,
    responses={422: {"model": PacketGenerationFailedOut}},
)
def generate_packet(
    finding_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.DRAFT_RECOVERY_PACKET),
    repository: Repository = Depends(get_repository),
) -> RecoveryPacketOut | JSONResponse:
    result = repository.generate_packet(
        ctx.user_id, require_facility(ctx), finding_id, generated_by=ctx.subject
    )
    if result is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")
    if isinstance(result, PacketGenerationFailed):
        payload = PacketGenerationFailedOut.from_domain(result)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=payload.model_dump(mode="json"),
        )
    return RecoveryPacketOut.from_domain(result)


@router.get("/findings/{finding_id}/packets", response_model=PacketListOut)
def list_packets(
    finding_id: UUID,
    ctx: AuthContext = require_permission(Action.READ_FINDING),
    repository: Repository = Depends(get_repository),
) -> PacketListOut:
    rows = repository.list_packets(
        ctx.user_id, require_facility(ctx), finding_id, actor=ctx.subject
    )
    return PacketListOut(items=[RecoveryPacketOut.from_domain(row) for row in rows])


@router.post("/packets/{packet_id}/approve", response_model=RecoveryPacketOut)
def approve_packet(
    packet_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.APPROVE_RECOVERY_PACKET),
    repository: Repository = Depends(get_repository),
) -> RecoveryPacketOut:
    row = repository.decide_packet(
        ctx.user_id, require_facility(ctx), packet_id, approve=True, decided_by=ctx.subject
    )
    if row is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="packet not found")
    return RecoveryPacketOut.from_domain(row)


@router.post("/packets/{packet_id}/reject", response_model=RecoveryPacketOut)
def reject_packet(
    packet_id: UUID,
    request: Request,
    ctx: AuthContext = require_permission(Action.APPROVE_RECOVERY_PACKET),
    repository: Repository = Depends(get_repository),
) -> RecoveryPacketOut:
    row = repository.decide_packet(
        ctx.user_id, require_facility(ctx), packet_id, approve=False, decided_by=ctx.subject
    )
    if row is None:
        record_not_found(request, ctx.subject)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="packet not found")
    return RecoveryPacketOut.from_domain(row)
