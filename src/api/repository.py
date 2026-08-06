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

from opentelemetry.trace import Tracer
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db import repository as db_repository
from db.models import Contract as ContractORM
from db.models import ContractVersion as ContractVersionORM
from db.models import Finding as FindingModel
from db.models import RecoveryPacket as RecoveryPacketModel
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
from domain.deadlines import calculate_appeal_deadline
from domain.money import Money, Rate
from domain.outcomes import (
    HistoricalOutcome,
    Outcome,
    calculate_confidence,
    validate_outcome_recording,
)
from domain.variance import RootCause
from ingestion.apply import IngestionOutcome
from ingestion.pipeline import DuplicateOutcome, ingest_file
from ingestion.virus_scan import VirusScanner
from observability.alert_state import IngestionOutcomeTracker, RollingWindowCounter
from observability.alerts import evaluate_ingestion_failure_alert, evaluate_unusual_phi_access_alert
from observability.metrics import Instruments
from observability.notifications import NotificationPort
from packets.drafter import PacketDrafter
from packets.prompt import PromptInput
from packets.service import generate_packet_draft
from packets.templates import PacketTemplate, select_template
from security.encryption import EnvelopeEncryptor
from security.phi_columns import decrypt_phi_field

_DEFAULT_TIMELY_FILING_DAYS = 90


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
class LoginCredentials:
    """What `POST /auth/login` (api/routes/auth.py) needs to verify a
    subject's password and TOTP code -- never returned to a client.
    `mfa_secret` is already decrypted (PostgresRepository does that
    itself, same convention as patient name/member id on FindingDetail);
    either field is None if that credential was never provisioned, which
    the login route treats identically to "wrong"."""

    subject: str
    role: str
    password_hash: str | None
    mfa_secret: str | None


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
    outcome: str | None
    amount_recovered: str | None
    outcome_recorded_by: str | None
    outcome_recorded_at: datetime | None


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
    confidence_score: str | None


@dataclass(frozen=True, slots=True)
class RecordOutcomeInput:
    outcome: str
    amount_recovered: Decimal | None


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
class RecoveryPacketSummary:
    id: uuid.UUID
    finding_id: uuid.UUID
    status: str
    draft_text: str
    deadline: date
    generated_by: str
    generated_at: datetime
    decided_by: str | None
    decided_at: datetime | None


@dataclass(frozen=True, slots=True)
class PacketGenerationFailed:
    finding_id: uuid.UUID
    attempts: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccessEventSummary:
    occurred_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str
    purpose: str | None


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
    def ping(self) -> bool: ...

    def get_user_by_subject(self, subject: str) -> UserRecord | None: ...

    def get_login_credentials(self, subject: str) -> LoginCredentials | None: ...

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
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
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

    def generate_packet(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, generated_by: str
    ) -> RecoveryPacketSummary | PacketGenerationFailed | None: ...

    def list_packets(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]: ...

    def decide_packet(
        self, tenant_id: uuid.UUID, packet_id: uuid.UUID, *, approve: bool, decided_by: str
    ) -> RecoveryPacketSummary | None: ...

    def get_claim_access_history(
        self, tenant_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]: ...

    def record_finding_outcome(
        self,
        tenant_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        data: RecordOutcomeInput,
        recorded_by: str,
    ) -> FindingSummary | None: ...


def _packet_to_summary(row: RecoveryPacketModel) -> RecoveryPacketSummary:
    return RecoveryPacketSummary(
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


def _template_from_json(data: dict[str, object] | None) -> PacketTemplate | None:
    if data is None:
        return None
    return PacketTemplate(
        salutation=str(data["salutation"]),
        letterhead=str(data["letterhead"]),
        closing=str(data["closing"]),
        footer_legal_text=str(data["footer_legal_text"]),
    )


def _lookup_payer_id(session: Session, contract_version_id: uuid.UUID | None) -> str | None:
    """`Claim`/`Finding` never carry payer identity directly -- it's only
    reachable via contract_version -> contract, and a finding with no
    contract_version (root_cause UNPRICED_CODE) has no payer to look up
    at all. Shared by confidence-score lookup and packet generation's
    existing contract-attribute lookup."""
    if contract_version_id is None:
        return None
    version = session.get(ContractVersionORM, contract_version_id)
    if version is None:
        return None
    contract = session.get(ContractORM, version.contract_id)
    return contract.payer_id if contract is not None else None


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
        outcome=row.outcome,
        amount_recovered=None if row.amount_recovered is None else str(row.amount_recovered),
        outcome_recorded_by=row.outcome_recorded_by,
        outcome_recorded_at=row.outcome_recorded_at,
    )


class PostgresRepository:
    """Real adapter. Every tenant-scoped method opens its own
    `tenant_session` -- callers pass a `sessionmaker`, not an open
    `Session`, so each call gets a fresh transaction scoped to the
    caller-supplied tenant_id (never a client-supplied one -- see
    api/auth.py)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        drafter: PacketDrafter,
        encryptor: EnvelopeEncryptor,
        tracer: Tracer | None = None,
        instruments: Instruments | None = None,
        notifier: NotificationPort | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._drafter = drafter
        self._encryptor = encryptor
        self._tracer = tracer
        self._instruments = instruments
        # F-11 (docs/audit/REGISTER.md): `notifier` is None by default,
        # same "additive, never required" contract `tracer`/`instruments`
        # already have -- every test written before this fix keeps
        # constructing a PostgresRepository without it. The two trackers
        # below are always built (cheap, in-memory, no live effect until
        # something actually calls .record()), not also optional --
        # nothing needs to substitute a different one, only whether
        # alerts get *dispatched* anywhere depends on `notifier`.
        self._notifier = notifier
        self._phi_access_tracker = RollingWindowCounter(window_seconds=300)
        self._ingestion_alert_tracker = IngestionOutcomeTracker()

    def _record_phi_access(self, actor: str) -> None:
        """Call right after every `db_repository.write_phi_access_log` --
        F-11's `evaluate_unusual_phi_access_alert`, fed a real rolling
        count instead of nothing."""
        if self._notifier is None:
            return
        count = self._phi_access_tracker.record(actor)
        alert = evaluate_unusual_phi_access_alert(
            actor=actor, access_count=count, window_description="in the last 5 minutes"
        )
        if alert is not None:
            self._notifier.notify(alert)

    def ping(self) -> bool:
        """For GET /readyz -- no tenant context needed. Queries
        `alembic_version` rather than a bare `SELECT 1`, so this proves
        both that the DB is reachable AND that migrations have actually
        run -- a freshly provisioned, unmigrated database (no
        `alembic_version` table, or an empty one) now correctly reports
        not-ready instead of a false "ready" that a `SELECT 1` alone would
        give. This is what makes the existing staging smoke test (which
        already polls /readyz) catch a deploy that skipped migrations --
        see F-03, docs/audit/REGISTER.md. Any exception here means "not
        ready", not a crash: callers catch and translate to a 503."""
        with self._session_factory() as session:
            session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).one()
        return True

    def get_user_by_subject(self, subject: str) -> UserRecord | None:
        with self._session_factory() as session:
            user = db_repository.get_user_by_subject(session, subject)
            if user is None:
                return None
            return UserRecord(tenant_id=user.tenant_id, role=user.role, subject=user.subject)

    def get_login_credentials(self, subject: str) -> LoginCredentials | None:
        with self._session_factory() as session:
            user = db_repository.get_user_by_subject(session, subject)
            if user is None:
                return None
            return LoginCredentials(
                subject=user.subject,
                role=user.role,
                password_hash=user.password_hash,
                mfa_secret=decrypt_phi_field(self._encryptor, user.mfa_secret_encrypted),
            )

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
            outcome = ingest_file(
                session,
                tenant_id,
                content=content,
                source=source,
                uploaded_by=uploaded_by,
                scanner=scanner,
                encryptor=self._encryptor,
                tracer=self._tracer,
                instruments=self._instruments,
            )
        # F-11 (docs/audit/REGISTER.md): outside the transaction -- this
        # is pure in-memory bookkeeping, not a DB write, so there's no
        # reason to hold the transaction open for it. DuplicateOutcome is
        # excluded, matching record_ingestion_outcome's own metrics call
        # a few lines up the stack in ingestion.pipeline.ingest_file.
        if isinstance(outcome, IngestionOutcome) and self._notifier is not None:
            quarantined_count, total_count = self._ingestion_alert_tracker.record(
                str(tenant_id), quarantined=(outcome.status == "quarantined")
            )
            alert = evaluate_ingestion_failure_alert(
                quarantined_count=quarantined_count, total_count=total_count
            )
            if alert is not None:
                self._notifier.notify(alert)
        return outcome

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
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> FindingDetail | None:
        with tenant_session(self._session_factory, tenant_id) as session:
            detail = db_repository.get_finding_detail(session, tenant_id, finding_id)
            if detail is None:
                return None
            db_repository.write_phi_access_log(
                session,
                tenant_id,
                actor=actor,
                claim_id=detail.claim.id,
                purpose="finding_detail_view",
            )
            self._record_phi_access(actor)
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
            payer_id = _lookup_payer_id(session, detail.finding.contract_version_id)
            confidence_score = None
            if payer_id is not None:
                historical_rows = db_repository.list_historical_outcomes(
                    session, tenant_id, payer_id, detail.finding.root_cause
                )
                historical = [
                    HistoricalOutcome(Outcome(row.outcome))
                    for row in historical_rows
                    if row.id != finding_id
                ]
                confidence = calculate_confidence(historical)
                confidence_score = None if confidence is None else str(confidence.as_decimal())
            return FindingDetail(
                summary=_finding_to_summary(detail.finding),
                evidence=detail.finding.evidence,
                patient_control_number=detail.claim.patient_control_number,
                payer_claim_control_number=detail.claim.payer_claim_control_number,
                date_of_service=detail.claim.date_of_service,
                patient_name=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_name_encrypted
                ),
                patient_member_id=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_member_id_encrypted
                ),
                service_line=service_line,
                adjustments=adjustments,
                confidence_score=confidence_score,
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

    def generate_packet(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, generated_by: str
    ) -> RecoveryPacketSummary | PacketGenerationFailed | None:
        with tenant_session(self._session_factory, tenant_id) as session:
            detail = db_repository.get_finding_detail(session, tenant_id, finding_id)
            if detail is None:
                return None
            db_repository.write_phi_access_log(
                session,
                tenant_id,
                actor=generated_by,
                claim_id=detail.claim.id,
                purpose="packet_generation",
            )
            self._record_phi_access(generated_by)

            timely_filing_days = _DEFAULT_TIMELY_FILING_DAYS
            template_override: PacketTemplate | None = None
            if detail.finding.contract_version_id is not None:
                version = session.get(ContractVersionORM, detail.finding.contract_version_id)
                contract = (
                    session.get(ContractORM, version.contract_id) if version is not None else None
                )
                if contract is not None:
                    timely_filing_days = contract.timely_filing_days
                    template_override = _template_from_json(contract.packet_template)
            template = select_template(template_override)
            deadline = calculate_appeal_deadline(detail.claim.date_of_service, timely_filing_days)

            prompt_input = PromptInput(
                payer_claim_control_number=detail.claim.payer_claim_control_number,
                procedure_code=detail.finding.procedure_code,
                date_of_service=detail.claim.date_of_service,
                expected_allowed=(
                    "0.00"
                    if detail.finding.expected_allowed is None
                    else str(detail.finding.expected_allowed)
                ),
                actual_allowed=str(detail.finding.actual_allowed),
                shortfall=str(detail.finding.shortfall),
                root_cause=detail.finding.root_cause,
                evidence=detail.finding.evidence,
                patient_name=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_name_encrypted
                ),
                patient_member_id=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_member_id_encrypted
                ),
            )

            result = generate_packet_draft(prompt_input, template, self._drafter)

            for _rejection in result.rejections:
                db_repository.write_audit_log(
                    session,
                    tenant_id,
                    actor=generated_by,
                    action="packet_draft_rejected",
                    resource_type="finding",
                    resource_id=str(finding_id),
                    phi_accessed=False,
                )

            if not result.success or result.final_text is None:
                db_repository.write_audit_log(
                    session,
                    tenant_id,
                    actor=generated_by,
                    action="packet_generation_failed",
                    resource_type="finding",
                    resource_id=str(finding_id),
                    phi_accessed=False,
                )
                return PacketGenerationFailed(
                    finding_id=finding_id,
                    attempts=result.attempts,
                    reasons=tuple(r.reason for r in result.rejections),
                )

            row = db_repository.create_recovery_packet(
                session,
                tenant_id,
                finding_id,
                draft_text=result.final_text,
                deadline=deadline,
                generated_by=generated_by,
            )
            db_repository.write_audit_log(
                session,
                tenant_id,
                actor=generated_by,
                action="packet_generated",
                resource_type="recovery_packet",
                resource_id=str(row.id),
                phi_accessed=True,
            )
            return _packet_to_summary(row)

    def list_packets(
        self, tenant_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]:
        with tenant_session(self._session_factory, tenant_id) as session:
            finding = session.get(FindingModel, finding_id)
            if finding is not None:
                db_repository.write_phi_access_log(
                    session,
                    tenant_id,
                    actor=actor,
                    claim_id=finding.claim_id,
                    purpose="packet_list_view",
                )
                self._record_phi_access(actor)
            rows = db_repository.list_recovery_packets_for_finding(session, tenant_id, finding_id)
            return [_packet_to_summary(row) for row in rows]

    def get_claim_access_history(
        self, tenant_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]:
        with tenant_session(self._session_factory, tenant_id) as session:
            events = db_repository.get_claim_access_history(session, tenant_id, claim_id)
            return tuple(
                AccessEventSummary(
                    occurred_at=event.occurred_at,
                    actor=event.actor,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    purpose=event.purpose,
                )
                for event in events
            )

    def record_finding_outcome(
        self,
        tenant_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        data: RecordOutcomeInput,
        recorded_by: str,
    ) -> FindingSummary | None:
        with tenant_session(self._session_factory, tenant_id) as session:
            row = session.get(FindingModel, finding_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            existing_outcome = Outcome(row.outcome) if row.outcome is not None else None
            validate_outcome_recording(RootCause[row.root_cause], existing_outcome)
            updated = db_repository.record_finding_outcome(
                session,
                tenant_id,
                finding_id,
                outcome=data.outcome,
                amount_recovered=data.amount_recovered,
                recorded_by=recorded_by,
            )
            # F-12 (docs/audit/REGISTER.md): this writes findings.outcome/
            # amount_recovered -- a PHI-bearing table, including a dollar
            # amount -- CLAUDE.md rule 5 has no exceptions. Same pattern
            # decide_packet already uses right after its own DB write.
            db_repository.write_audit_log(
                session,
                tenant_id,
                actor=recorded_by,
                action="finding_outcome_recorded",
                resource_type="finding",
                resource_id=str(finding_id),
                phi_accessed=True,
            )
            return _finding_to_summary(updated)

    def decide_packet(
        self, tenant_id: uuid.UUID, packet_id: uuid.UUID, *, approve: bool, decided_by: str
    ) -> RecoveryPacketSummary | None:
        with tenant_session(self._session_factory, tenant_id) as session:
            existing = session.get(RecoveryPacketModel, packet_id)
            if existing is None or existing.tenant_id != tenant_id:
                return None
            row = db_repository.decide_recovery_packet(
                session, tenant_id, packet_id, approve=approve, decided_by=decided_by
            )
            db_repository.write_audit_log(
                session,
                tenant_id,
                actor=decided_by,
                action="packet_approved" if approve else "packet_rejected",
                resource_type="recovery_packet",
                resource_id=str(packet_id),
                phi_accessed=False,
            )
            return _packet_to_summary(row)
