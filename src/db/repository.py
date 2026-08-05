"""Thin persistence functions bridging the Postgres schema and the Phase 1
domain layer.

Money and Rate values cross the boundary as `Decimal` in both directions --
never `float` (CLAUDE.md rule 2). JSONB rule sub-structures on
contract_versions store rates as decimal strings, not JSON numbers, for the
same reason: a JSON number is parsed back as `float` by every standard
JSON decoder, silently reintroducing the exact class of bug `domain.money`
exists to make impossible.

Every function here expects to run inside a `db.tenancy.tenant_session()`
transaction -- RLS enforces tenant isolation at the database level; nothing
here adds its own `WHERE tenant_id = ...` filtering on top, since Phase 3's
whole point is that RLS is the actual boundary, not app-level filtering.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.access_history import AccessEvent, merge_access_history
from db.models import Adjustment as AdjustmentModel
from db.models import AuditLog as AuditLogModel
from db.models import Claim as ClaimModel
from db.models import Contract as ContractModel
from db.models import ContractVersion as ContractVersionModel
from db.models import FeeScheduleLine as FeeScheduleLineModel
from db.models import Finding as FindingModel
from db.models import PHIAccessLog as PHIAccessLogModel
from db.models import RecoveryPacket as RecoveryPacketModel
from db.models import Remittance as RemittanceModel
from db.models import ServiceLine as ServiceLineModel
from db.models import Tenant as TenantModel
from db.models import User as UserModel
from db.rules_version import RULES_VERSION
from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    CaseRateGroup,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
    find_effective_contract,
)
from domain.money import Money, Rate
from domain.variance import Finding

# --- JSONB (de)serialization for the contract rule sub-structures ------------


def _rate_to_str(rate: Rate) -> str:
    return str(rate.as_decimal())


def _mppr_rule_to_json(rule: MPPRRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "second_procedure_rate": _rate_to_str(rule.second_procedure_rate),
        "third_and_subsequent_rate": _rate_to_str(rule.third_and_subsequent_rate),
        "exempt_codes": sorted(rule.exempt_codes),
    }


def _mppr_rule_from_json(data: dict[str, Any]) -> MPPRRule:
    return MPPRRule(
        enabled=bool(data["enabled"]),
        second_procedure_rate=Rate(str(data["second_procedure_rate"])),
        third_and_subsequent_rate=Rate(str(data["third_and_subsequent_rate"])),
        exempt_codes=frozenset(str(c) for c in data["exempt_codes"]),
    )


def _bilateral_rule_to_json(rule: BilateralRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "total_rate": _rate_to_str(rule.total_rate),
        "convention": rule.convention.value,
    }


def _bilateral_rule_from_json(data: dict[str, Any]) -> BilateralRule:
    return BilateralRule(
        enabled=bool(data["enabled"]),
        total_rate=Rate(str(data["total_rate"])),
        convention=BilateralConvention(str(data["convention"])),
    )


def _assistant_surgeon_rule_to_json(rule: AssistantSurgeonRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "rate": _rate_to_str(rule.rate),
        "applicable_modifiers": sorted(rule.applicable_modifiers),
    }


def _assistant_surgeon_rule_from_json(data: dict[str, Any]) -> AssistantSurgeonRule:
    return AssistantSurgeonRule(
        enabled=bool(data["enabled"]),
        rate=Rate(str(data["rate"])),
        applicable_modifiers=frozenset(str(m) for m in data["applicable_modifiers"]),
    )


def _implant_carveout_rule_to_json(rule: ImplantCarveoutRule) -> dict[str, Any]:
    return {
        "enabled": rule.enabled,
        "procedure_codes": sorted(rule.procedure_codes),
        "revenue_codes": sorted(rule.revenue_codes),
    }


def _implant_carveout_rule_from_json(data: dict[str, Any]) -> ImplantCarveoutRule:
    return ImplantCarveoutRule(
        enabled=bool(data["enabled"]),
        procedure_codes=frozenset(str(c) for c in data["procedure_codes"]),
        revenue_codes=frozenset(str(c) for c in data["revenue_codes"]),
    )


def _case_rate_groups_to_json(groups: tuple[CaseRateGroup, ...]) -> list[dict[str, Any]]:
    return [
        {
            "trigger_procedure_codes": sorted(group.trigger_procedure_codes),
            "flat_rate": str(group.flat_rate.as_decimal()),
            "includes_implants": group.includes_implants,
        }
        for group in groups
    ]


def _case_rate_groups_from_json(data: list[Any]) -> tuple[CaseRateGroup, ...]:
    return tuple(
        CaseRateGroup(
            trigger_procedure_codes=frozenset(str(c) for c in group["trigger_procedure_codes"]),
            flat_rate=Money(str(group["flat_rate"])),
            includes_implants=bool(group["includes_implants"]),
        )
        for group in data
    )


def _contract_version_to_domain(
    session: Session, row: ContractVersionModel, payer_id: str
) -> ContractVersion:
    fee_schedule_rows = (
        session.execute(
            select(FeeScheduleLineModel).where(
                FeeScheduleLineModel.contract_version_id == row.id
            )
        )
        .scalars()
        .all()
    )
    fee_schedule = {r.procedure_code: Money(r.allowed_amount) for r in fee_schedule_rows}
    return ContractVersion(
        payer_id=payer_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        default_pricing_method=PricingMethod(row.default_pricing_method),
        fee_schedule=fee_schedule,
        percent_of_charge_rate=(
            Rate(row.percent_of_charge_rate) if row.percent_of_charge_rate is not None else None
        ),
        case_rate_groups=_case_rate_groups_from_json(row.case_rate_groups),
        mppr_rule=_mppr_rule_from_json(row.mppr_rule),
        bilateral_rule=_bilateral_rule_from_json(row.bilateral_rule),
        assistant_surgeon_rule=_assistant_surgeon_rule_from_json(row.assistant_surgeon_rule),
        implant_carveout_rule=_implant_carveout_rule_from_json(row.implant_carveout_rule),
    )


# --- Tenants -------------------------------------------------------------------


def create_tenant(session: Session, name: str) -> TenantModel:
    tenant = TenantModel(name=name)
    session.add(tenant)
    session.flush()
    return tenant


# --- Users (ungated, like tenants -- see db.models.User's docstring) -----------


def get_user_by_subject(session: Session, subject: str) -> UserModel | None:
    """Plain, non-tenant-scoped lookup -- this is how a request's tenant
    context gets bootstrapped in the first place (src/api/auth.py), so it
    must run outside `tenant_session` rather than inside it."""
    return session.execute(
        select(UserModel).where(UserModel.subject == subject, UserModel.deleted_at.is_(None))
    ).scalar_one_or_none()


def create_user(session: Session, tenant_id: uuid.UUID, *, subject: str, role: str) -> UserModel:
    user = UserModel(tenant_id=tenant_id, subject=subject, role=role)
    session.add(user)
    session.flush()
    return user


# --- Effective-dated contracts --------------------------------------------------


def create_contract(
    session: Session, tenant_id: uuid.UUID, payer_id: str, name: str
) -> ContractModel:
    contract = ContractModel(tenant_id=tenant_id, payer_id=payer_id, name=name)
    session.add(contract)
    session.flush()
    return contract


def get_contract_by_payer_id(
    session: Session, tenant_id: uuid.UUID, payer_id: str
) -> ContractModel | None:
    """For Phase 7's `timely_filing_days`/`packet_template` -- both live on
    `Contract`, not `ContractVersion` (see that model's docstring)."""
    return session.execute(
        select(ContractModel).where(
            ContractModel.tenant_id == tenant_id, ContractModel.payer_id == payer_id
        )
    ).scalar_one_or_none()


def create_contract_version(
    session: Session,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    version: ContractVersion,
) -> ContractVersionModel:
    row = ContractVersionModel(
        tenant_id=tenant_id,
        contract_id=contract_id,
        effective_from=version.effective_from,
        effective_to=version.effective_to,
        default_pricing_method=version.default_pricing_method.value,
        percent_of_charge_rate=(
            version.percent_of_charge_rate.as_decimal()
            if version.percent_of_charge_rate is not None
            else None
        ),
        mppr_rule=_mppr_rule_to_json(version.mppr_rule),
        bilateral_rule=_bilateral_rule_to_json(version.bilateral_rule),
        assistant_surgeon_rule=_assistant_surgeon_rule_to_json(version.assistant_surgeon_rule),
        implant_carveout_rule=_implant_carveout_rule_to_json(version.implant_carveout_rule),
        case_rate_groups=_case_rate_groups_to_json(version.case_rate_groups),
    )
    session.add(row)
    session.flush()
    for procedure_code, amount in version.fee_schedule.items():
        session.add(
            FeeScheduleLineModel(
                tenant_id=tenant_id,
                contract_version_id=row.id,
                procedure_code=procedure_code,
                allowed_amount=amount.as_decimal(),
            )
        )
    session.flush()
    return row


def get_effective_contract_version(
    session: Session, tenant_id: uuid.UUID, payer_id: str, date_of_service: date
) -> ContractVersion | None:
    """Loads every version of `payer_id`'s contract for this tenant and
    hands off to the already-tested domain.contract.find_effective_contract
    -- Phase 3 adds no new date-effective logic, it only proves the Phase 1
    logic still picks the right version when the data comes from Postgres."""
    rows = (
        session.execute(
            select(ContractVersionModel)
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(ContractModel.tenant_id == tenant_id, ContractModel.payer_id == payer_id)
            .order_by(ContractVersionModel.effective_from.desc())
        )
        .scalars()
        .all()
    )
    versions = [_contract_version_to_domain(session, row, payer_id) for row in rows]
    return find_effective_contract(payer_id, date_of_service, versions)


def list_contract_versions(
    session: Session, tenant_id: uuid.UUID, payer_id: str
) -> list[tuple[uuid.UUID, ContractVersion]]:
    """Every version of `payer_id`'s contract for this tenant, undated --
    callers that need to price several claims against possibly-different
    dates of service (ingestion) load once and pick per-claim via
    domain.contract.find_effective_contract themselves, rather than paying
    for a fresh query per claim. Returned paired with each version's row id
    since findings need to record which contract_version priced them
    (CLAUDE.md rule 8 / docs/PHASES.md: reproducible after a rules change),
    and the domain ContractVersion dataclass itself deliberately carries no
    DB identity."""
    rows = (
        session.execute(
            select(ContractVersionModel)
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(ContractModel.tenant_id == tenant_id, ContractModel.payer_id == payer_id)
            .order_by(ContractVersionModel.effective_from.desc())
        )
        .scalars()
        .all()
    )
    return [(row.id, _contract_version_to_domain(session, row, payer_id)) for row in rows]


def list_contracts(
    session: Session, tenant_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> tuple[list[ContractModel], int]:
    """All contracts for this tenant across every payer -- unlike
    `list_contract_versions`/`get_effective_contract_version`, which are
    scoped to one payer at a time for pricing lookups. For contract
    management's list view."""
    total = session.execute(
        select(func.count()).select_from(ContractModel).where(ContractModel.tenant_id == tenant_id)
    ).scalar_one()
    rows = (
        session.execute(
            select(ContractModel)
            .where(ContractModel.tenant_id == tenant_id)
            .order_by(ContractModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- Idempotent remittances -----------------------------------------------------


def record_remittance_if_new(
    session: Session,
    tenant_id: uuid.UUID,
    file_hash: str,
    *,
    source: str,
    uploaded_by: str,
) -> tuple[RemittanceModel, bool]:
    """Returns (row, is_new). `is_new is False` means this exact file was
    already ingested for this tenant -- the caller must not create any new
    claims or findings from it."""
    stmt = (
        pg_insert(RemittanceModel)
        .values(
            tenant_id=tenant_id,
            file_hash=file_hash,
            source=source,
            uploaded_by=uploaded_by,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "file_hash"])
        .returning(RemittanceModel.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()
    if inserted_id is not None:
        row = session.get(RemittanceModel, inserted_id)
        if row is None:
            raise RuntimeError(
                f"remittance {inserted_id} was just inserted but cannot be re-read "
                "in the same transaction -- this should never happen"
            )
        return row, True

    existing = session.execute(
        select(RemittanceModel).where(
            RemittanceModel.tenant_id == tenant_id, RemittanceModel.file_hash == file_hash
        )
    ).scalar_one()
    return existing, False


# --- Claims and findings ---------------------------------------------------------


def create_claim(
    session: Session,
    tenant_id: uuid.UUID,
    remittance_id: uuid.UUID,
    *,
    patient_control_number: str,
    payer_claim_control_number: str,
    status: str,
    date_of_service: date,
    total_charge: Decimal,
    total_paid_reported: Decimal,
    patient_responsibility: Decimal,
    patient_name_encrypted: str | None = None,
    patient_member_id_encrypted: str | None = None,
) -> ClaimModel:
    # Callers pass already-encrypted values (security.phi_columns) -- this
    # layer only ever persists opaque strings, never plaintext PHI. See
    # ingestion.apply for the encryption call site.
    claim = ClaimModel(
        tenant_id=tenant_id,
        remittance_id=remittance_id,
        patient_control_number=patient_control_number,
        payer_claim_control_number=payer_claim_control_number,
        status=status,
        date_of_service=date_of_service,
        total_charge=total_charge,
        total_paid_reported=total_paid_reported,
        patient_responsibility=patient_responsibility,
        patient_name_encrypted=patient_name_encrypted,
        patient_member_id_encrypted=patient_member_id_encrypted,
    )
    session.add(claim)
    session.flush()
    return claim


def create_service_line(
    session: Session,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    *,
    line_index: int,
    procedure_code: str,
    modifiers: Sequence[str],
    revenue_code: str | None,
    charge: Decimal,
    allowed: Decimal,
    paid_computed: Decimal,
    service_date: date | None,
) -> ServiceLineModel:
    line = ServiceLineModel(
        tenant_id=tenant_id,
        claim_id=claim_id,
        line_index=line_index,
        procedure_code=procedure_code,
        modifiers=list(modifiers),
        revenue_code=revenue_code,
        charge=charge,
        allowed=allowed,
        paid_computed=paid_computed,
        service_date=service_date,
    )
    session.add(line)
    session.flush()
    return line


def create_adjustment(
    session: Session,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    service_line_id: uuid.UUID | None,
    *,
    group_code: str,
    reason_code: str,
    amount: Decimal,
) -> AdjustmentModel:
    adjustment = AdjustmentModel(
        tenant_id=tenant_id,
        claim_id=claim_id,
        service_line_id=service_line_id,
        group_code=group_code,
        reason_code=reason_code,
        amount=amount,
    )
    session.add(adjustment)
    session.flush()
    return adjustment


def update_remittance_status(
    session: Session,
    tenant_id: uuid.UUID,
    remittance_id: uuid.UUID,
    *,
    status: str,
    quarantine_reason: str | None = None,
) -> RemittanceModel:
    row = session.execute(
        select(RemittanceModel).where(
            RemittanceModel.tenant_id == tenant_id, RemittanceModel.id == remittance_id
        )
    ).scalar_one()
    row.status = status
    row.quarantine_reason = quarantine_reason
    session.flush()
    return row


def save_findings(
    session: Session,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    service_line_ids: Mapping[int, uuid.UUID],
    contract_version_id: uuid.UUID | None,
    findings: Sequence[Finding],
) -> list[FindingModel]:
    rows: list[FindingModel] = []
    for finding in findings:
        row = FindingModel(
            tenant_id=tenant_id,
            claim_id=claim_id,
            service_line_id=service_line_ids[finding.line_index],
            contract_version_id=contract_version_id,
            line_index=finding.line_index,
            procedure_code=finding.procedure_code,
            expected_allowed=(
                finding.expected_allowed.as_decimal()
                if finding.expected_allowed is not None
                else None
            ),
            actual_allowed=finding.actual_allowed.as_decimal(),
            shortfall=finding.shortfall.as_decimal(),
            root_cause=finding.root_cause.name,
            evidence=finding.evidence,
            rule_version=RULES_VERSION,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def list_findings_by_payer_claim_control_number(
    session: Session, tenant_id: uuid.UUID, payer_claim_control_number: str
) -> list[FindingModel]:
    """Findings belonging to any claim previously ingested under this payer
    claim control number -- used by ingestion to net a reversal (CLP02=22)
    against what it's reversing. Not scoped by remittance_id: a reversal
    typically arrives in a different file than the original payment.

    Explicitly filtered by tenant_id (in addition to whatever RLS enforces
    on the connection) -- this was previously accepted-but-unused, meaning
    a payer_claim_control_number collision across tenants would leak
    findings across tenants on any connection where RLS wasn't already
    doing the job (e.g. a superuser/owner connection)."""
    return list(
        session.execute(
            select(FindingModel)
            .join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
            .where(
                ClaimModel.tenant_id == tenant_id,
                ClaimModel.payer_claim_control_number == payer_claim_control_number,
            )
        )
        .scalars()
        .all()
    )


# --- Outcomes and confidence scoring (Phase 12) -----------------------------------


def record_finding_outcome(
    session: Session,
    tenant_id: uuid.UUID,
    finding_id: uuid.UUID,
    *,
    outcome: str,
    amount_recovered: Decimal | None,
    recorded_by: str,
) -> FindingModel:
    """The human-recording step -- callers must call
    domain.outcomes.validate_outcome_recording first (this function does
    not re-check that an outcome isn't already set or that the finding
    isn't a CORRECT_NO_VARIANCE one; that's pure domain logic, not a
    database concern)."""
    finding = session.execute(
        select(FindingModel).where(
            FindingModel.tenant_id == tenant_id, FindingModel.id == finding_id
        )
    ).scalar_one()
    finding.outcome = outcome
    finding.amount_recovered = amount_recovered
    finding.outcome_recorded_by = recorded_by
    finding.outcome_recorded_at = datetime.now(UTC)
    session.flush()
    return finding


def list_historical_outcomes(
    session: Session, tenant_id: uuid.UUID, payer_id: str, root_cause: str
) -> list[FindingModel]:
    """Every already-decided finding (outcome recorded) for this payer and
    root cause -- domain.outcomes.calculate_confidence turns this into a
    score for a new finding matching the same two dimensions. Payer
    identity comes via contract_version -> contract, since Claim itself
    doesn't carry payer_id directly; findings with no contract_version
    (UNPRICED_CODE) can never match a payer this way and are correctly
    excluded."""
    return list(
        session.execute(
            select(FindingModel)
            .join(
                ContractVersionModel,
                FindingModel.contract_version_id == ContractVersionModel.id,
            )
            .join(ContractModel, ContractVersionModel.contract_id == ContractModel.id)
            .where(
                FindingModel.tenant_id == tenant_id,
                ContractModel.payer_id == payer_id,
                FindingModel.root_cause == root_cause,
                FindingModel.outcome.is_not(None),
            )
        )
        .scalars()
        .all()
    )


def list_findings_past_deadline_without_outcome(
    session: Session, tenant_id: uuid.UUID, as_of: date
) -> list[FindingModel]:
    """Findings whose approved recovery packet's appeal deadline has
    passed with no outcome recorded yet. A human confirms these as
    `expired` (domain.outcomes.Outcome.EXPIRED) -- this query only
    surfaces candidates, it never writes anything itself."""
    return list(
        session.execute(
            select(FindingModel)
            .join(RecoveryPacketModel, RecoveryPacketModel.finding_id == FindingModel.id)
            .where(
                FindingModel.tenant_id == tenant_id,
                FindingModel.outcome.is_(None),
                RecoveryPacketModel.status == "approved",
                RecoveryPacketModel.deadline < as_of,
            )
        )
        .scalars()
        .all()
    )


@dataclass(frozen=True, slots=True)
class FindingDetail:
    """Full evidence chain for a single finding -- Finding joined with its
    Claim, ServiceLine, and every Adjustment on that claim (claim-level and
    line-level). Repository functions elsewhere in this module return bare
    ORM rows; this one is a small dataclass because a finding detail
    response is inherently a join of four tables, not one row."""

    finding: FindingModel
    claim: ClaimModel
    service_line: ServiceLineModel
    adjustments: list[AdjustmentModel]


def list_findings(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    root_cause: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_shortfall: Decimal | None = None,
    remittance_id: uuid.UUID | None = None,
    claim_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[FindingModel], int]:
    """Filters never include a patient identifier -- callers (the API
    layer) must not expose one as a query parameter, since query strings
    land in access logs (CLAUDE.md rule 6)."""
    conditions = [FindingModel.tenant_id == tenant_id]
    if root_cause is not None:
        conditions.append(FindingModel.root_cause == root_cause)
    if min_shortfall is not None:
        conditions.append(FindingModel.shortfall >= min_shortfall)
    if claim_id is not None:
        conditions.append(FindingModel.claim_id == claim_id)
    if date_from is not None or date_to is not None or remittance_id is not None:
        query_base = select(FindingModel).join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
        count_base = (
            select(func.count())
            .select_from(FindingModel)
            .join(ClaimModel, FindingModel.claim_id == ClaimModel.id)
        )
        if date_from is not None:
            conditions.append(ClaimModel.date_of_service >= date_from)
        if date_to is not None:
            conditions.append(ClaimModel.date_of_service <= date_to)
        if remittance_id is not None:
            conditions.append(ClaimModel.remittance_id == remittance_id)
    else:
        query_base = select(FindingModel)
        count_base = select(func.count()).select_from(FindingModel)

    total = session.execute(count_base.where(*conditions)).scalar_one()
    rows = (
        session.execute(
            query_base.where(*conditions)
            .order_by(FindingModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def get_finding_detail(
    session: Session, tenant_id: uuid.UUID, finding_id: uuid.UUID
) -> FindingDetail | None:
    finding = session.execute(
        select(FindingModel).where(
            FindingModel.tenant_id == tenant_id, FindingModel.id == finding_id
        )
    ).scalar_one_or_none()
    if finding is None:
        return None
    claim = session.get(ClaimModel, finding.claim_id)
    service_line = session.get(ServiceLineModel, finding.service_line_id)
    if claim is None or service_line is None:
        raise RuntimeError(
            f"finding {finding_id} references a claim or service_line that no "
            "longer exists -- FK constraints should make this impossible"
        )
    adjustments = list(
        session.execute(
            select(AdjustmentModel).where(AdjustmentModel.claim_id == finding.claim_id)
        )
        .scalars()
        .all()
    )
    return FindingDetail(
        finding=finding, claim=claim, service_line=service_line, adjustments=adjustments
    )


# --- Audit log -------------------------------------------------------------------


def write_audit_log(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    phi_accessed: bool = False,
    source_ip: str | None = None,
    request_id: str | None = None,
) -> AuditLogModel:
    entry = AuditLogModel(
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        phi_accessed=phi_accessed,
        source_ip=source_ip,
        request_id=request_id,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_log(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AuditLogModel], int]:
    conditions = [AuditLogModel.tenant_id == tenant_id]
    if actor is not None:
        conditions.append(AuditLogModel.actor == actor)
    if action is not None:
        conditions.append(AuditLogModel.action == action)
    if resource_type is not None:
        conditions.append(AuditLogModel.resource_type == resource_type)
    if date_from is not None:
        conditions.append(AuditLogModel.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLogModel.occurred_at <= date_to)

    total = session.execute(
        select(func.count()).select_from(AuditLogModel).where(*conditions)
    ).scalar_one()
    rows = (
        session.execute(
            select(AuditLogModel)
            .where(*conditions)
            .order_by(AuditLogModel.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


# --- Recovery packets (Phase 7) --------------------------------------------------


def create_recovery_packet(
    session: Session,
    tenant_id: uuid.UUID,
    finding_id: uuid.UUID,
    *,
    draft_text: str,
    deadline: date,
    generated_by: str,
) -> RecoveryPacketModel:
    packet = RecoveryPacketModel(
        tenant_id=tenant_id,
        finding_id=finding_id,
        status="draft",
        draft_text=draft_text,
        deadline=deadline,
        generated_by=generated_by,
    )
    session.add(packet)
    session.flush()
    return packet


def decide_recovery_packet(
    session: Session,
    tenant_id: uuid.UUID,
    packet_id: uuid.UUID,
    *,
    approve: bool,
    decided_by: str,
) -> RecoveryPacketModel:
    """The human-approval step -- never automatic. `approve=False` records
    a rejection, not a deletion; the draft and who declined it stay on
    the record."""
    packet = session.execute(
        select(RecoveryPacketModel).where(
            RecoveryPacketModel.tenant_id == tenant_id, RecoveryPacketModel.id == packet_id
        )
    ).scalar_one()
    packet.status = "approved" if approve else "rejected"
    packet.decided_by = decided_by
    packet.decided_at = datetime.now(UTC)
    session.flush()
    return packet


def list_recovery_packets_for_finding(
    session: Session, tenant_id: uuid.UUID, finding_id: uuid.UUID
) -> list[RecoveryPacketModel]:
    return list(
        session.execute(
            select(RecoveryPacketModel)
            .where(
                RecoveryPacketModel.tenant_id == tenant_id,
                RecoveryPacketModel.finding_id == finding_id,
            )
            .order_by(RecoveryPacketModel.generated_at.desc())
        )
        .scalars()
        .all()
    )


# --- PHI access log and the auditor-facing claim access history (Phase 8) ------


def write_phi_access_log(
    session: Session, tenant_id: uuid.UUID, *, actor: str, claim_id: uuid.UUID, purpose: str
) -> PHIAccessLogModel:
    entry = PHIAccessLogModel(tenant_id=tenant_id, actor=actor, claim_id=claim_id, purpose=purpose)
    session.add(entry)
    session.flush()
    return entry


def list_audit_log_by_resource_ids(
    session: Session, tenant_id: uuid.UUID, resource_type: str, resource_ids: Sequence[str]
) -> list[AuditLogModel]:
    if not resource_ids:
        return []
    return list(
        session.execute(
            select(AuditLogModel).where(
                AuditLogModel.tenant_id == tenant_id,
                AuditLogModel.resource_type == resource_type,
                AuditLogModel.resource_id.in_(resource_ids),
            )
        )
        .scalars()
        .all()
    )


def get_claim_access_history(
    session: Session, tenant_id: uuid.UUID, claim_id: uuid.UUID
) -> tuple[AccessEvent, ...]:
    """Auditor-facing report: "who accessed this claim's data, when, and
    why." A claim's findings and recovery packets reference it only
    indirectly, so this resolves their ids first, gathers `audit_log`
    entries across all three resource types plus every `phi_access_log`
    entry, converts each row to a plain `AccessEvent`, and hands off to
    the pure `merge_access_history` for the chronological merge."""
    finding_ids = list(
        session.execute(
            select(FindingModel.id).where(
                FindingModel.tenant_id == tenant_id, FindingModel.claim_id == claim_id
            )
        )
        .scalars()
        .all()
    )
    packet_ids = (
        list(
            session.execute(
                select(RecoveryPacketModel.id).where(
                    RecoveryPacketModel.tenant_id == tenant_id,
                    RecoveryPacketModel.finding_id.in_(finding_ids),
                )
            )
            .scalars()
            .all()
        )
        if finding_ids
        else []
    )

    audit_rows = (
        list_audit_log_by_resource_ids(session, tenant_id, "claim", [str(claim_id)])
        + list_audit_log_by_resource_ids(
            session, tenant_id, "finding", [str(fid) for fid in finding_ids]
        )
        + list_audit_log_by_resource_ids(
            session, tenant_id, "recovery_packet", [str(pid) for pid in packet_ids]
        )
    )
    phi_rows = list(
        session.execute(
            select(PHIAccessLogModel).where(
                PHIAccessLogModel.tenant_id == tenant_id, PHIAccessLogModel.claim_id == claim_id
            )
        )
        .scalars()
        .all()
    )

    audit_events = [
        AccessEvent(
            occurred_at=row.occurred_at,
            actor=row.actor,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            purpose=None,
        )
        for row in audit_rows
    ]
    phi_events = [
        AccessEvent(
            occurred_at=row.accessed_at,
            actor=row.actor,
            action="phi_access",
            resource_type="claim",
            resource_id=str(row.claim_id),
            purpose=row.purpose,
        )
        for row in phi_rows
    ]
    return merge_access_history(audit_events, phi_events)
