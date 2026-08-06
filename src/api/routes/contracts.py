"""GET /contracts (list), POST /contracts (create), POST
/contracts/{contract_id}/versions (add an effective-dated version).
Viewing is broadly allowed (Action.READ_CONTRACT); managing is admin-only
(Action.MANAGE_CONTRACT), per security/rbac.py.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.auth import AuthContext, get_repository, require_permission
from api.rate_limit import enforce_rate_limit
from api.repository import ContractVersionInput, Page, Repository, RuleInput
from api.schemas import (
    ContractListOut,
    ContractSummaryOut,
    ContractVersionOut,
    CreateContractIn,
    CreateContractVersionIn,
)
from security.rbac import Action

router = APIRouter(dependencies=[Depends(enforce_rate_limit)])


def _page(
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
) -> Page:
    return Page(limit=limit, offset=offset)


@router.get("/contracts", response_model=ContractListOut)
def list_contracts(
    page: Page = Depends(_page),
    ctx: AuthContext = require_permission(Action.READ_CONTRACT),
    repository: Repository = Depends(get_repository),
) -> ContractListOut:
    result = repository.list_contracts(ctx.tenant_id, page=page)
    return ContractListOut.from_domain(result)


@router.post("/contracts", response_model=ContractSummaryOut, status_code=201)
def create_contract(
    body: CreateContractIn,
    ctx: AuthContext = require_permission(Action.MANAGE_CONTRACT),
    repository: Repository = Depends(get_repository),
) -> ContractSummaryOut:
    row = repository.create_contract(ctx.tenant_id, payer_id=body.payer_id, name=body.name)
    return ContractSummaryOut.from_domain(row)


@router.post(
    "/contracts/{contract_id}/versions", response_model=ContractVersionOut, status_code=201
)
def create_contract_version(
    contract_id: UUID,
    body: CreateContractVersionIn,
    ctx: AuthContext = require_permission(Action.MANAGE_CONTRACT),
    repository: Repository = Depends(get_repository),
) -> ContractVersionOut:
    data = ContractVersionInput(
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        default_pricing_method=body.default_pricing_method,
        fee_schedule=body.fee_schedule,
        percent_of_charge_rate_percent=body.percent_of_charge_rate_percent,
        rules=RuleInput(
            mppr_enabled=body.mppr_rule.enabled,
            mppr_second_rate_percent=body.mppr_rule.second_procedure_rate_percent,
            mppr_third_rate_percent=body.mppr_rule.third_and_subsequent_rate_percent,
            mppr_exempt_codes=frozenset(body.mppr_rule.exempt_codes),
            bilateral_enabled=body.bilateral_rule.enabled,
            bilateral_total_rate_percent=body.bilateral_rule.total_rate_percent,
            assistant_enabled=body.assistant_surgeon_rule.enabled,
            assistant_rate_percent=body.assistant_surgeon_rule.rate_percent,
            assistant_modifiers=frozenset(body.assistant_surgeon_rule.applicable_modifiers),
            implant_enabled=body.implant_carveout_rule.enabled,
            implant_procedure_codes=frozenset(body.implant_carveout_rule.procedure_codes),
            implant_revenue_codes=frozenset(body.implant_carveout_rule.revenue_codes),
        ),
    )
    version_id = repository.create_contract_version(ctx.tenant_id, contract_id, data)
    return ContractVersionOut(id=version_id)
