"""API-facing repository port, same port/adapter shape as
security.kms/ingestion.sources: route handlers depend on the `Repository`
Protocol, never on SQLAlchemy directly.

`PostgresRepository` wraps `db.repository` + `db.tenancy.tenant_session`.
A `FakeRepository` (tests/api/fakes.py, test-only) is the other adapter --
tenant-partitioned in-memory storage, so the full role x endpoint x tenant
authorization matrix can run as real, passing tests in an environment with
no live Postgres, the same trick ingestion.plan/apply used in Phase 5.

Every dataclass here carries money as `str`, never `float`
(CLAUDE.md rule 2) -- these are what route handlers serialize directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from db import repository as db_repository
from db.models import Finding as FindingModel
from db.tenancy import tenant_session
from domain.contract import (
    AssistantSurgeonRule,
    BilateralConvention,
    BilateralRule,
    ContractVersion,
    ImplantCarveoutRule,
    MPPRRule,
    PricingMethod,
)
from domain.money import Money, Rate
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.virus_scan import VirusScanner


@dataclass(frozen=True, slots=True)
class PagedResult[T]:
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class Page:
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UserRecord:
    tenant_id: uuid.UUID
    role: str
    subject: str


@dataclass(frozen=True, slots=True)
class FindingSummary:
    id: uuid.UUID
    claim_id: uuid.UUID
    line_index: int
    procedure_code: str
    expected_allowed: str | None
    actual_allowed: str
    shortfall: str
    root_cause: str
    rule_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ServiceLineInfo:
    line_index: int
    procedure_code: str
    modifiers: list[str]
    charge: str
    allowed: str
    paid_computed: str
    service_date: date | None


@dataclass(frozen=True, slots=True)
class AdjustmentInfo:
    group_code: str
    reason_code: str
    amount: str


@dataclass(frozen=True, slots=True)
class FindingDetail:
    summary: FindingSummary
    evidence: str
    patient_control_number: str
    payer_claim_control_number: str
    date_of_service: date
    patient_name: str | None
    patient_member_id: str | None
    service_line: ServiceLineInfo
    adjustments: list[AdjustmentInfo]


@dataclass(frozen=True, slots=True)
class FindingFilters:
    root_cause: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_shortfall: Decimal | None = None
    remittance_id: uuid.UUID | None = None
    claim_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ContractSummary:
    id: uuid.UUID
    payer_id: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditLogEntry:
    id: uuid.UUID
    actor: str
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    source_ip: str | None
    phi_accessed: bool
    request_id: str | None


@dataclass(frozen=True, slots=True)
class AuditLogFilters:
    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True, slots=True)
class RuleInput:
    """Bare, undecorated inputs for the rule sub-structures on a contract
    version -- the API's Pydantic schema (api.schemas) maps onto this
    before it reaches the repository layer, keeping domain.contract's
    richer dataclasses (Rate/Money-typed) out of the HTTP boundary."""

    mppr_enabled: bool = False
    mppr_second_rate_percent: str = "50"
    mppr_third_rate_percent: str = "25"
    mppr_exempt_codes: frozenset[str] = field(default_factory=frozenset)
    bilateral_enabled: bool = False
    bilateral_total_rate_percent: str = "150"
    assistant_enabled: bool = False
    assistant_rate_percent: str = "16"
    assistant_modifiers: frozenset[str] = field(default_factory=frozenset)
    implant_enabled: bool = False
    implant_procedure_codes: frozenset[str] = field(default_factory=frozenset)
    implant_revenue_codes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ContractVersionInput:
    effective_from: date
    effective_to: date | None
    default_pricing_method: str
    fee_schedule: dict[str, str]
    percent_of_charge_rate_percent: str | None
    rules: RuleInput


def _rule_input_to_contract_version(data: ContractVersionInput) -> ContractVersion:
    # payer_id is a required field on the domain dataclass, but
    # db.repository.create_contract_version never reads it back off --
    # payer_id lives on the parent Contract row instead, joined via
    # contract_id. This placeholder is discarded before persistence.
    return ContractVersion(
        payer_id="",
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        default_pricing_method=PricingMethod(data.default_pricing_method),
        fee_schedule={code: Money(amount) for code, amount in data.fee_schedule.items()},
        percent_of_charge_rate=(
            Rate.percent(data.percent_of_charge_rate_percent)
            if data.percent_of_charge_rate_percent is not None
            else None
        ),
        case_rate_groups=(),
        mppr_rule=MPPRRule(
            enabled=data.rules.mppr_enabled,
            second_procedure_rate=Rate.percent(data.rules.mppr_second_rate_percent),
            third_and_subsequent_rate=Rate.percent(data.rules.mppr_third_rate_percent),
            exempt_codes=data.rules.mppr_exempt_codes,
        ),
        bilateral_rule=BilateralRule(
            enabled=data.rules.bilateral_enabled,
            total_rate=Rate.percent(data.rules.bilateral_total_rate_percent),
            convention=BilateralConvention.SINGLE_LINE_150_PCT,
        ),
        assistant_surgeon_rule=AssistantSurgeonRule(
            enabled=data.rules.assistant_enabled,
            rate=Rate.percent(data.rules.assistant_rate_percent),
            applicable_modifiers=data.rules.assistant_modifiers,
        ),
        implant_carveout_rule=ImplantCarveoutRule(
            enabled=data.rules.implant_enabled,
            procedure_codes=data.rules.implant_procedure_codes,
            revenue_codes=data.rules.implant_revenue_codes,
        ),
    )


class Repository(Protocol):
    def get_user_by_subject(self, subject: str) -> UserRecord | None: ...

    def ingest_remittance(
        self,
        tenant_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome: ...

    def list_findings(
        self, tenant_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]: ...

    def get_finding_detail(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID
    ) -> FindingDetail | None: ...

    def list_contracts(
        self, tenant_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ContractSummary]: ...

    def create_contract(
        self, tenant_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary: ...

    def create_contract_version(
        self, tenant_id: uuid.UUID, contract_id: uuid.UUID, data: ContractVersionInput
    ) -> uuid.UUID: ...

    def list_audit_log(
        self, tenant_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]: ...

    def write_audit_log(
        self,
        tenant_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None: ...


def _finding_to_summary(row: FindingModel) -> FindingSummary:
    return FindingSummary(
        id=row.id,
        claim_id=row.claim_id,
        line_index=row.line_index,
        procedure_code=row.procedure_code,
        expected_allowed=None if row.expected_allowed is None else str(row.expected_allowed),
        actual_allowed=str(row.actual_allowed),
        shortfall=str(row.shortfall),
        root_cause=row.root_cause,
        rule_version=row.rule_version,
        created_at=row.created_at,
    )


class PostgresRepository:
    """Real adapter. Every tenant-scoped method opens its own
    `tenant_session` -- callers pass a `sessionmaker`, not an open
    `Session`, so each call gets a fresh transaction scoped to the
    caller-supplied tenant_id (never a client-supplied one -- see
    api/auth.py)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get_user_by_subject(self, subject: str) -> UserRecord | None:
        with self._session_factory() as session:
            user = db_repository.get_user_by_subject(session, subject)
            if user is None:
                return None
            return UserRecord(tenant_id=user.tenant_id, role=user.role, subject=user.subject)

    def ingest_remittance(
        self,
        tenant_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome:
        with tenant_session(self._session_factory, tenant_id) as session:
            return ingest_file(
                session,
                tenant_id,
                content=content,
                source=source,
                uploaded_by=uploaded_by,
                scanner=scanner,
            )

    def list_findings(
        self, tenant_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]:
        with tenant_session(self._session_factory, tenant_id) as session:
            rows, total = db_repository.list_findings(
                session,
                tenant_id,
                root_cause=filters.root_cause,
                date_from=filters.date_from,
                date_to=filters.date_to,
                min_shortfall=filters.min_shortfall,
                remittance_id=filters.remittance_id,
                claim_id=filters.claim_id,
                limit=page.limit,
                offset=page.offset,
            )
            items = [_finding_to_summary(row) for row in rows]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def get_finding_detail(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID
    ) -> FindingDetail | None:
        with tenant_session(self._session_factory, tenant_id) as session:
            detail = db_repository.get_finding_detail(session, tenant_id, finding_id)
            if detail is None:
                return None
            adjustments = [
                AdjustmentInfo(
                    group_code=a.group_code, reason_code=a.reason_code, amount=str(a.amount)
                )
                for a in detail.adjustments
            ]
            service_line = ServiceLineInfo(
                line_index=detail.service_line.line_index,
                procedure_code=detail.service_line.procedure_code,
                modifiers=list(detail.service_line.modifiers),
                charge=str(detail.service_line.charge),
                allowed=str(detail.service_line.allowed),
                paid_computed=str(detail.service_line.paid_computed),
                service_date=detail.service_line.service_date,
            )
            return FindingDetail(
                summary=_finding_to_summary(detail.finding),
                evidence=detail.finding.evidence,
                patient_control_number=detail.claim.patient_control_number,
                payer_claim_control_number=detail.claim.payer_claim_control_number,
                date_of_service=detail.claim.date_of_service,
                patient_name=detail.claim.patient_name,
                patient_member_id=detail.claim.patient_member_id,
                service_line=service_line,
                adjustments=adjustments,
            )

    def list_contracts(
        self, tenant_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ContractSummary]:
        with tenant_session(self._session_factory, tenant_id) as session:
            rows, total = db_repository.list_contracts(
                session, tenant_id, limit=page.limit, offset=page.offset
            )
            items = [
                ContractSummary(
                    id=row.id, payer_id=row.payer_id, name=row.name, created_at=row.created_at
                )
                for row in rows
            ]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def create_contract(
        self, tenant_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary:
        with tenant_session(self._session_factory, tenant_id) as session:
            row = db_repository.create_contract(session, tenant_id, payer_id, name)
            return ContractSummary(
                id=row.id, payer_id=row.payer_id, name=row.name, created_at=row.created_at
            )

    def create_contract_version(
        self, tenant_id: uuid.UUID, contract_id: uuid.UUID, data: ContractVersionInput
    ) -> uuid.UUID:
        version = _rule_input_to_contract_version(data)
        with tenant_session(self._session_factory, tenant_id) as session:
            row = db_repository.create_contract_version(session, tenant_id, contract_id, version)
            return row.id

    def list_audit_log(
        self, tenant_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]:
        with tenant_session(self._session_factory, tenant_id) as session:
            rows, total = db_repository.list_audit_log(
                session,
                tenant_id,
                actor=filters.actor,
                action=filters.action,
                resource_type=filters.resource_type,
                date_from=filters.date_from,
                date_to=filters.date_to,
                limit=page.limit,
                offset=page.offset,
            )
            items = [
                AuditLogEntry(
                    id=row.id,
                    actor=row.actor,
                    action=row.action,
                    resource_type=row.resource_type,
                    resource_id=row.resource_id,
                    occurred_at=row.occurred_at,
                    source_ip=row.source_ip,
                    phi_accessed=row.phi_accessed,
                    request_id=row.request_id,
                )
                for row in rows
            ]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def write_audit_log(
        self,
        tenant_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None:
        with tenant_session(self._session_factory, tenant_id) as session:
            db_repository.write_audit_log(
                session,
                tenant_id,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                phi_accessed=phi_accessed,
                request_id=request_id,
            )
