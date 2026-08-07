"""Pydantic v2 request/response models. Every money field is `str`, never
`float` (CLAUDE.md rule 2) -- `api.repository`'s dataclasses already carry
money as `str(Decimal)`, so these models just mirror that shape without
ever routing a dollar amount through a float-capable type.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from api.repository import (
    AcceptedInvitation,
    AccessEventSummary,
    ApiKeyListItem,
    ApiKeySummary,
    AuditLogEntry,
    ContractSummary,
    FindingDetail,
    FindingSummary,
    InvitationPreview,
    InvitationSummary,
    OrgMemberSummary,
    PacketGenerationFailed,
    PagedResult,
    RecoveryPacketSummary,
)
from security.rbac import Role


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class LoginIn(BaseModel):
    subject: str
    password: str
    totp_code: str


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class FindingSummaryOut(BaseModel):
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
    outcome: str | None
    amount_recovered: str | None
    outcome_recorded_by: str | None
    outcome_recorded_at: datetime | None

    @classmethod
    def from_domain(cls, row: FindingSummary) -> FindingSummaryOut:
        return cls(
            id=row.id,
            claim_id=row.claim_id,
            line_index=row.line_index,
            procedure_code=row.procedure_code,
            expected_allowed=row.expected_allowed,
            actual_allowed=row.actual_allowed,
            shortfall=row.shortfall,
            root_cause=row.root_cause,
            rule_version=row.rule_version,
            created_at=row.created_at,
            outcome=row.outcome,
            amount_recovered=row.amount_recovered,
            outcome_recorded_by=row.outcome_recorded_by,
            outcome_recorded_at=row.outcome_recorded_at,
        )


class FindingListOut(BaseModel):
    items: list[FindingSummaryOut]
    page: PageMeta

    @classmethod
    def from_domain(cls, result: PagedResult[FindingSummary]) -> FindingListOut:
        return cls(
            items=[FindingSummaryOut.from_domain(item) for item in result.items],
            page=PageMeta(total=result.total, limit=result.limit, offset=result.offset),
        )


class ServiceLineOut(BaseModel):
    line_index: int
    procedure_code: str
    modifiers: list[str]
    charge: str
    allowed: str
    paid_computed: str
    service_date: date | None


class AdjustmentOut(BaseModel):
    group_code: str
    reason_code: str
    amount: str


class FindingDetailOut(BaseModel):
    finding: FindingSummaryOut
    evidence: str
    patient_control_number: str
    payer_claim_control_number: str
    date_of_service: date
    patient_name: str | None
    patient_member_id: str | None
    service_line: ServiceLineOut
    adjustments: list[AdjustmentOut]
    confidence_score: str | None

    @classmethod
    def from_domain(cls, detail: FindingDetail) -> FindingDetailOut:
        return cls(
            finding=FindingSummaryOut.from_domain(detail.summary),
            evidence=detail.evidence,
            patient_control_number=detail.patient_control_number,
            payer_claim_control_number=detail.payer_claim_control_number,
            date_of_service=detail.date_of_service,
            patient_name=detail.patient_name,
            patient_member_id=detail.patient_member_id,
            confidence_score=detail.confidence_score,
            service_line=ServiceLineOut(
                line_index=detail.service_line.line_index,
                procedure_code=detail.service_line.procedure_code,
                modifiers=detail.service_line.modifiers,
                charge=detail.service_line.charge,
                allowed=detail.service_line.allowed,
                paid_computed=detail.service_line.paid_computed,
                service_date=detail.service_line.service_date,
            ),
            adjustments=[
                AdjustmentOut(
                    group_code=a.group_code, reason_code=a.reason_code, amount=a.amount
                )
                for a in detail.adjustments
            ],
        )


class RecordOutcomeIn(BaseModel):
    outcome: Literal["recovered", "denied", "abandoned", "expired"]
    # str, not Decimal -- same money-field convention as everywhere else in
    # this module; parsed to Decimal in the route handler before it
    # reaches api.repository.RecordOutcomeInput.
    amount_recovered: str | None = None


class IngestionOutcomeOut(BaseModel):
    remittance_id: uuid.UUID
    status: str
    claims_created: int
    findings_created: int
    reconciliation_mismatches: int


class ContractSummaryOut(BaseModel):
    id: uuid.UUID
    payer_id: str
    name: str
    created_at: datetime

    @classmethod
    def from_domain(cls, row: ContractSummary) -> ContractSummaryOut:
        return cls(id=row.id, payer_id=row.payer_id, name=row.name, created_at=row.created_at)


class ContractListOut(BaseModel):
    items: list[ContractSummaryOut]
    page: PageMeta

    @classmethod
    def from_domain(cls, result: PagedResult[ContractSummary]) -> ContractListOut:
        return cls(
            items=[ContractSummaryOut.from_domain(item) for item in result.items],
            page=PageMeta(total=result.total, limit=result.limit, offset=result.offset),
        )


class OrgMemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    role: str
    scope: str
    facility_ids: list[uuid.UUID]
    created_at: datetime

    @classmethod
    def from_domain(cls, row: OrgMemberSummary) -> OrgMemberOut:
        return cls(
            membership_id=row.membership_id,
            user_id=row.user_id,
            subject=row.subject,
            role=row.role.value,
            scope=row.scope,
            facility_ids=list(row.facility_ids),
            created_at=row.created_at,
        )


class OrgMemberListOut(BaseModel):
    items: list[OrgMemberOut]
    page: PageMeta

    @classmethod
    def from_domain(cls, result: PagedResult[OrgMemberSummary]) -> OrgMemberListOut:
        return cls(
            items=[OrgMemberOut.from_domain(item) for item in result.items],
            page=PageMeta(total=result.total, limit=result.limit, offset=result.offset),
        )


class CreateInvitationIn(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    role: Role
    scope: Literal["ALL_FACILITIES", "SPECIFIC_FACILITIES"] = "ALL_FACILITIES"
    facility_ids: list[uuid.UUID] = Field(default_factory=list)


class InvitationCreatedOut(BaseModel):
    id: uuid.UUID
    token: str
    subject: str
    role: str
    scope: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, row: InvitationSummary) -> InvitationCreatedOut:
        return cls(
            id=row.id,
            token=row.token,
            subject=row.subject,
            role=row.role.value,
            scope=row.scope,
            expires_at=row.expires_at,
        )


class InvitationPreviewOut(BaseModel):
    subject: str
    role: str
    scope: str
    status: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, row: InvitationPreview) -> InvitationPreviewOut:
        return cls(
            subject=row.subject,
            role=row.role.value,
            scope=row.scope,
            status=row.status,
            expires_at=row.expires_at,
        )


class AcceptInvitationIn(BaseModel):
    password: str = Field(min_length=8)


class AcceptInvitationOut(BaseModel):
    user_id: uuid.UUID
    membership_id: uuid.UUID
    mfa_secret: str
    mfa_provisioning_uri: str

    @classmethod
    def from_domain(cls, row: AcceptedInvitation) -> AcceptInvitationOut:
        return cls(
            user_id=row.user_id,
            membership_id=row.membership_id,
            mfa_secret=row.mfa_secret,
            mfa_provisioning_uri=row.mfa_provisioning_uri,
        )


class ConfirmInvitationMfaIn(BaseModel):
    totp_code: str = Field(min_length=6, max_length=6)


class ConfirmInvitationMfaOut(BaseModel):
    verified: bool


class CreateApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scope: Literal["ALL_FACILITIES", "SPECIFIC_FACILITIES"] = "ALL_FACILITIES"
    facility_ids: list[uuid.UUID] = Field(default_factory=list)


class ApiKeyCreatedOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    scope: str
    facility_ids: list[uuid.UUID]
    expires_at: datetime

    @classmethod
    def from_domain(cls, row: ApiKeySummary) -> ApiKeyCreatedOut:
        return cls(
            id=row.id,
            key=row.key,
            name=row.name,
            scope=row.scope,
            facility_ids=list(row.facility_ids),
            expires_at=row.expires_at,
        )


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None

    @classmethod
    def from_domain(cls, row: ApiKeyListItem) -> ApiKeyOut:
        return cls(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
        )


class ApiKeyListOut(BaseModel):
    items: list[ApiKeyOut]
    page: PageMeta

    @classmethod
    def from_domain(cls, result: PagedResult[ApiKeyListItem]) -> ApiKeyListOut:
        return cls(
            items=[ApiKeyOut.from_domain(item) for item in result.items],
            page=PageMeta(total=result.total, limit=result.limit, offset=result.offset),
        )


class CreateContractIn(BaseModel):
    payer_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class MPPRRuleIn(BaseModel):
    enabled: bool = False
    second_procedure_rate_percent: str = "50"
    third_and_subsequent_rate_percent: str = "25"
    exempt_codes: list[str] = Field(default_factory=list)


class BilateralRuleIn(BaseModel):
    enabled: bool = False
    total_rate_percent: str = "150"


class AssistantSurgeonRuleIn(BaseModel):
    enabled: bool = False
    rate_percent: str = "16"
    applicable_modifiers: list[str] = Field(default_factory=list)


class ImplantCarveoutRuleIn(BaseModel):
    enabled: bool = False
    procedure_codes: list[str] = Field(default_factory=list)
    revenue_codes: list[str] = Field(default_factory=list)


class CreateContractVersionIn(BaseModel):
    effective_from: date
    effective_to: date | None = None
    default_pricing_method: str = "fee_schedule"
    fee_schedule: dict[str, str] = Field(default_factory=dict)
    percent_of_charge_rate_percent: str | None = None
    mppr_rule: MPPRRuleIn = Field(default_factory=MPPRRuleIn)
    bilateral_rule: BilateralRuleIn = Field(default_factory=BilateralRuleIn)
    assistant_surgeon_rule: AssistantSurgeonRuleIn = Field(default_factory=AssistantSurgeonRuleIn)
    implant_carveout_rule: ImplantCarveoutRuleIn = Field(default_factory=ImplantCarveoutRuleIn)


class ContractVersionOut(BaseModel):
    id: uuid.UUID


class AuditLogEntryOut(BaseModel):
    id: uuid.UUID
    actor: str
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    source_ip: str | None
    phi_accessed: bool
    request_id: str | None

    @classmethod
    def from_domain(cls, row: AuditLogEntry) -> AuditLogEntryOut:
        return cls(
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


class AuditLogListOut(BaseModel):
    items: list[AuditLogEntryOut]
    page: PageMeta

    @classmethod
    def from_domain(cls, result: PagedResult[AuditLogEntry]) -> AuditLogListOut:
        return cls(
            items=[AuditLogEntryOut.from_domain(item) for item in result.items],
            page=PageMeta(total=result.total, limit=result.limit, offset=result.offset),
        )


class RecoveryPacketOut(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    status: str
    draft_text: str
    deadline: date
    generated_by: str
    generated_at: datetime
    decided_by: str | None
    decided_at: datetime | None

    @classmethod
    def from_domain(cls, row: RecoveryPacketSummary) -> RecoveryPacketOut:
        return cls(
            id=row.id,
            finding_id=row.finding_id,
            status=row.status,
            draft_text=row.draft_text,
            deadline=row.deadline,
            generated_by=row.generated_by,
            generated_at=row.generated_at,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
        )


class PacketListOut(BaseModel):
    items: list[RecoveryPacketOut]


class PacketGenerationFailedOut(BaseModel):
    finding_id: uuid.UUID
    attempts: int
    reasons: list[str]

    @classmethod
    def from_domain(cls, failure: PacketGenerationFailed) -> PacketGenerationFailedOut:
        return cls(
            finding_id=failure.finding_id, attempts=failure.attempts, reasons=list(failure.reasons)
        )


class AccessEventOut(BaseModel):
    occurred_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str
    purpose: str | None


class AccessHistoryOut(BaseModel):
    items: list[AccessEventOut]

    @classmethod
    def from_domain(cls, events: Sequence[AccessEventSummary]) -> AccessHistoryOut:
        return cls(
            items=[
                AccessEventOut(
                    occurred_at=event.occurred_at,
                    actor=event.actor,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    purpose=event.purpose,
                )
                for event in events
            ]
        )


class ErrorOut(BaseModel):
    error: str
    message: str
    request_id: str
