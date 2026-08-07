"""API-facing repository port, same port/adapter shape as
security.kms/ingestion.sources: route handlers depend on the `Repository`
Protocol, never on SQLAlchemy directly.

`PostgresRepository` wraps `db.repository` + `db.access.access_session`.
A `FakeRepository` (tests/api/fakes.py, test-only) is the other adapter --
facility-partitioned in-memory storage, so the full role x endpoint x
facility authorization matrix can run as real, passing tests in an
environment with no live Postgres, the same trick ingestion.plan/apply
used in Phase 5.

Every dataclass here carries money as `str`, never `float`
(CLAUDE.md rule 2) -- these are what route handlers serialize directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from opentelemetry.trace import Tracer
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db import repository as db_repository
from db.access import access_session
from db.models import Contract as ContractORM
from db.models import ContractVersion as ContractVersionORM
from db.models import Finding as FindingModel
from db.models import RecoveryPacket as RecoveryPacketModel
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
from security.mfa import generate_enrollment_secret, provisioning_uri, verify_code
from security.passwords import hash_password
from security.phi_columns import decrypt_phi_field, encrypt_phi_field
from security.phi_masking import mask_patient_fields
from security.rbac import Role
from security.tokens import generate_api_key, generate_token, hash_token

_DEFAULT_TIMELY_FILING_DAYS = 90
_INVITATION_TTL = timedelta(days=7)
_API_KEY_TTL = timedelta(days=365)


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
    id: uuid.UUID
    subject: str


@dataclass(frozen=True, slots=True)
class LoginCredentials:
    """What `POST /auth/login` (api/routes/auth.py) needs to verify a
    subject's password and TOTP code -- never returned to a client.
    `mfa_secret` is already decrypted (PostgresRepository does that
    itself, same convention as patient name/member id on FindingDetail);
    either field is None if that credential was never provisioned, which
    the login route treats identically to "wrong". No `role` here (Phase
    4, `docs/MASTER-BUILD-PROMPT-V2.md`) -- role is per-membership, not a
    single value a login step can carry, and is resolved fresh from
    `memberships` on every request instead (`api/auth.py`).
    `default_org_id` is `None` only for a user with zero memberships at
    all (not yet provisioned onto any org) -- the login route must refuse
    to issue a session in that case, since there is nothing to scope it
    to. Picking *which* org becomes active at login is a real UX decision
    (a user may hold several memberships) that Phase 5's login/switch-org
    flow owns; this is a deliberate, documented stopgap that always picks
    one deterministically rather than blocking Phase 4 on building that
    flow early."""

    user_id: uuid.UUID
    subject: str
    password_hash: str | None
    mfa_secret: str | None
    default_org_id: uuid.UUID | None


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
class OrgMemberSummary:
    """One row of `GET /organizations/members` (Phase 5 step 2).
    `facility_ids` is only meaningful when `scope == "SPECIFIC_FACILITIES"`
    -- empty for `ALL_FACILITIES`, where access is the full resolved
    subtree rather than an enumerated list (see `db.repository.OrgMember`'s
    docstring)."""

    membership_id: uuid.UUID
    user_id: uuid.UUID
    subject: str
    role: Role
    scope: str
    facility_ids: tuple[uuid.UUID, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InvitationSummary:
    """Returned once, at creation (`POST /invitations`) -- `token` is the
    raw, unhashed value; only its hash is ever persisted
    (`db.models.Invitation`'s docstring). There is no way to recover it
    after this response; a lost invitation must be re-sent (a fresh
    invitation, not a token-recovery flow -- there is no email-sending
    infrastructure in this codebase to "re-send" through anyway, see
    `docs/RUNBOOK.md`)."""

    id: uuid.UUID
    token: str
    subject: str
    role: Role
    scope: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class InvitationPreview:
    """What an anonymous visitor sees at `GET /invitations/{token}` before
    deciding to accept -- deliberately minimal (no org name/id; Phase 5's
    core subset has no public org-directory concern to weigh against
    exposing it, so this just doesn't)."""

    subject: str
    role: Role
    scope: str
    status: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    """Returned once, from `POST /invitations/{token}/accept` --
    `mfa_secret`/`mfa_provisioning_uri` are shown here and nowhere else
    (the secret is encrypted at rest immediately; this response is the
    only chance to scan the QR code / seed an authenticator app). See
    `PostgresRepository.accept_invitation`'s docstring for why
    `confirm-mfa` (the next step) is a stateless re-verification rather
    than a second required write."""

    user_id: uuid.UUID
    membership_id: uuid.UUID
    mfa_secret: str
    mfa_provisioning_uri: str


@dataclass(frozen=True, slots=True)
class ApiKeySummary:
    """Returned once, at creation (`POST /api-keys`) -- `key` is the raw,
    unhashed value; only its hash is ever persisted (`db.models.ApiKey`'s
    docstring). Same never-shown-again discipline as
    `InvitationSummary.token`: a lost key must be revoked and re-created,
    not recovered."""

    id: uuid.UUID
    key: str
    name: str
    scope: str
    facility_ids: tuple[uuid.UUID, ...]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyListItem:
    """`GET /api-keys` -- masked, never carries the raw key or its hash."""

    id: uuid.UUID
    name: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApiKeyAuthLookup:
    """What `api/auth.py` needs to turn a presented API key into an
    `AuthContext` -- `revoked_at`/`expires_at` let it reject a dead key
    before ever calling `resolve_membership_role`."""

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    name: str
    revoked_at: datetime | None
    expires_at: datetime


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
    """`user_id` (first positional param on every access-scoped method) is
    who this call runs as -- it's what `access_session` sets `app.user_id`
    to, which RLS's resolution functions key off. `facility_id`/`org_id`
    is the specific target within that user's resolved-accessible set
    (`AuthContext.facility_id`/`.org_id`, `api/auth.py`) -- required, not
    optional: Phase 4 does not build a "query across every facility I can
    reach in one call" API, only per-facility/per-org calls (see
    `docs/PROGRESS.md` for why that's a deliberate, documented scope
    limit, not an oversight). RLS still narrows *within* that target
    correctly even so -- a facility_id the caller can't actually reach
    returns nothing, never another org's data."""

    def ping(self) -> bool: ...

    def get_user_by_subject(self, subject: str) -> UserRecord | None: ...

    def get_login_credentials(self, subject: str) -> LoginCredentials | None: ...

    def resolve_membership_role(
        self, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> Role | None: ...

    def resolve_default_facility_id(
        self, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> uuid.UUID | None: ...

    def ingest_remittance(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome: ...

    def list_findings(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]: ...

    def get_finding_detail(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        actor: str,
        role: Role,
    ) -> FindingDetail | None: ...

    def list_contracts(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ContractSummary]: ...

    def list_org_members(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[OrgMemberSummary]: ...

    def revoke_membership(self, user_id: uuid.UUID, membership_id: uuid.UUID) -> bool: ...

    def create_invitation(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        subject: str,
        role: Role,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> InvitationSummary: ...

    def get_invitation_preview(self, token: str) -> InvitationPreview | None: ...

    def accept_invitation(self, token: str, *, password: str) -> AcceptedInvitation | None: ...

    def verify_invitation_mfa(self, token: str, *, totp_code: str) -> bool | None: ...

    def create_api_key(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        name: str,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> ApiKeySummary: ...

    def list_api_keys(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ApiKeyListItem]: ...

    def revoke_api_key(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> bool: ...

    def get_api_key_for_auth(self, raw_key: str) -> ApiKeyAuthLookup | None: ...

    def touch_api_key_last_used(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> None: ...

    def create_contract(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary: ...

    def create_contract_version(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        contract_id: uuid.UUID,
        data: ContractVersionInput,
    ) -> uuid.UUID: ...

    def list_audit_log(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]: ...

    def write_audit_log(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None: ...

    def generate_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        generated_by: str,
    ) -> RecoveryPacketSummary | PacketGenerationFailed | None: ...

    def list_packets(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]: ...

    def decide_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        packet_id: uuid.UUID,
        *,
        approve: bool,
        decided_by: str,
    ) -> RecoveryPacketSummary | None: ...

    def get_claim_access_history(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]: ...

    def record_finding_outcome(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
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
    """Real adapter. Every resolved-access method opens its own
    `access_session` -- callers pass a `sessionmaker`, not an open
    `Session`, so each call gets a fresh transaction scoped to the
    caller-supplied user_id (never a client-supplied one -- see
    api/auth.py). Facility/org id parameters below are what the caller
    already resolved access to; RLS (`resolve_accessible_facility_ids`/
    `resolve_accessible_org_ids`) is what actually enforces that the
    connected user may reach them at all."""

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
            return UserRecord(id=user.id, subject=user.subject)

    def get_login_credentials(self, subject: str) -> LoginCredentials | None:
        with self._session_factory() as session:
            user = db_repository.get_user_by_subject(session, subject)
            if user is None:
                return None
            mfa_secret = decrypt_phi_field(self._encryptor, user.mfa_secret_encrypted)
        with access_session(self._session_factory, user.id) as session:
            default_org_id = db_repository.get_default_membership_org_id(session, user.id)
        return LoginCredentials(
            user_id=user.id,
            subject=user.subject,
            password_hash=user.password_hash,
            mfa_secret=mfa_secret,
            default_org_id=default_org_id,
        )

    def resolve_membership_role(self, user_id: uuid.UUID, org_id: uuid.UUID) -> Role | None:
        with access_session(self._session_factory, user_id) as session:
            role = db_repository.resolve_membership_role(session, user_id, org_id)
            return None if role is None else Role(role)

    def resolve_default_facility_id(
        self, user_id: uuid.UUID, org_id: uuid.UUID
    ) -> uuid.UUID | None:
        with access_session(self._session_factory, user_id) as session:
            return db_repository.get_default_facility_id_for_org(session, user_id, org_id)

    def ingest_remittance(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        content: bytes,
        source: str,
        uploaded_by: str,
        scanner: VirusScanner,
    ) -> IngestionOutcome | DuplicateOutcome:
        with access_session(self._session_factory, user_id) as session:
            outcome = ingest_file(
                session,
                facility_id,
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
                str(facility_id), quarantined=(outcome.status == "quarantined")
            )
            alert = evaluate_ingestion_failure_alert(
                quarantined_count=quarantined_count, total_count=total_count
            )
            if alert is not None:
                self._notifier.notify(alert)
        return outcome

    def list_findings(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: FindingFilters, page: Page
    ) -> PagedResult[FindingSummary]:
        with access_session(self._session_factory, user_id) as session:
            rows, total = db_repository.list_findings(
                session,
                facility_id,
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
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        actor: str,
        role: Role,
    ) -> FindingDetail | None:
        with access_session(self._session_factory, user_id) as session:
            detail = db_repository.get_finding_detail(session, facility_id, finding_id)
            if detail is None:
                return None
            db_repository.write_phi_access_log(
                session,
                facility_id,
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
                    session, facility_id, payer_id, detail.finding.root_cause
                )
                historical = [
                    HistoricalOutcome(Outcome(row.outcome))
                    for row in historical_rows
                    if row.id != finding_id
                ]
                confidence = calculate_confidence(historical)
                confidence_score = None if confidence is None else str(confidence.as_decimal())
            # Phase 4 field-level PHI masking (security/phi_masking.py):
            # analyst reads amounts/codes but never unmasked patient
            # name/member id -- applied once, here, not per-route.
            patient_name, patient_member_id = mask_patient_fields(
                role,
                patient_name=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_name_encrypted
                ),
                patient_member_id=decrypt_phi_field(
                    self._encryptor, detail.claim.patient_member_id_encrypted
                ),
            )
            return FindingDetail(
                summary=_finding_to_summary(detail.finding),
                evidence=detail.finding.evidence,
                patient_control_number=detail.claim.patient_control_number,
                payer_claim_control_number=detail.claim.payer_claim_control_number,
                date_of_service=detail.claim.date_of_service,
                patient_name=patient_name,
                patient_member_id=patient_member_id,
                service_line=service_line,
                adjustments=adjustments,
                confidence_score=confidence_score,
            )

    def list_contracts(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ContractSummary]:
        with access_session(self._session_factory, user_id) as session:
            rows, total = db_repository.list_contracts(
                session, org_id, limit=page.limit, offset=page.offset
            )
            items = [
                ContractSummary(
                    id=row.id, payer_id=row.payer_id, name=row.name, created_at=row.created_at
                )
                for row in rows
            ]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def list_org_members(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[OrgMemberSummary]:
        with access_session(self._session_factory, user_id) as session:
            rows, total = db_repository.list_org_memberships(
                session, org_id, limit=page.limit, offset=page.offset
            )
            items = [
                OrgMemberSummary(
                    membership_id=row.membership.id,
                    user_id=row.membership.user_id,
                    subject=row.subject,
                    role=Role(row.membership.role),
                    scope=row.membership.scope,
                    facility_ids=tuple(row.facility_ids),
                    created_at=row.membership.created_at,
                )
                for row in rows
            ]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def revoke_membership(self, user_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
        with access_session(self._session_factory, user_id) as session:
            return db_repository.revoke_membership(session, membership_id)

    def create_invitation(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        subject: str,
        role: Role,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> InvitationSummary:
        raw_token = generate_token()
        expires_at = datetime.now(UTC) + _INVITATION_TTL
        with access_session(self._session_factory, user_id) as session:
            row = db_repository.create_invitation(
                session,
                org_id,
                subject=subject,
                role=role.value,
                scope=scope,
                invited_by=user_id,
                token_hash=hash_token(raw_token),
                expires_at=expires_at,
                facility_ids=facility_ids,
            )
            return InvitationSummary(
                id=row.id,
                token=raw_token,
                subject=row.subject,
                role=Role(row.role),
                scope=row.scope,
                expires_at=row.expires_at,
            )

    def get_invitation_preview(self, token: str) -> InvitationPreview | None:
        with self._session_factory() as session:
            row = db_repository.get_invitation_by_token_hash(session, hash_token(token))
        if row is None:
            return None
        return InvitationPreview(
            subject=row.subject,
            role=Role(row.role),
            scope=row.scope,
            status=row.status,
            expires_at=row.expires_at,
        )

    def accept_invitation(self, token: str, *, password: str) -> AcceptedInvitation | None:
        """Pre-checks status/expiry against the same preview read
        `get_invitation_preview` uses before calling the `SECURITY
        DEFINER` `accept_invitation` DB function -- that function
        re-checks the same conditions itself (under `FOR UPDATE`), which
        is the actual safety net against a concurrent accept racing this
        check; this pre-check just keeps the common (non-race) path a
        clean `None` -> 404/410 at the route layer instead of an
        exception. MFA secret generation happens here, not in the DB
        function -- `EnvelopeEncryptor` is an application-layer
        dependency the migration has no access to."""
        token_hash = hash_token(token)
        with self._session_factory() as session, session.begin():
            preview = db_repository.get_invitation_by_token_hash(session, token_hash)
            if (
                preview is None
                or preview.status != "pending"
                or preview.expires_at < datetime.now(UTC)
            ):
                return None
            mfa_secret = generate_enrollment_secret()
            mfa_secret_encrypted = encrypt_phi_field(self._encryptor, mfa_secret)
            if mfa_secret_encrypted is None:
                # encrypt_phi_field only returns None for a None input --
                # mfa_secret is a freshly generated string, never None.
                raise RuntimeError("encrypting a freshly generated MFA secret produced None")
            result = db_repository.accept_invitation(
                session,
                token_hash,
                password_hash=hash_password(password),
                mfa_secret_encrypted=mfa_secret_encrypted,
            )
        return AcceptedInvitation(
            user_id=result.user_id,
            membership_id=result.membership_id,
            mfa_secret=mfa_secret,
            mfa_provisioning_uri=provisioning_uri(mfa_secret, preview.subject),
        )

    def verify_invitation_mfa(self, token: str, *, totp_code: str) -> bool | None:
        with self._session_factory() as session:
            preview = db_repository.get_invitation_by_token_hash(session, hash_token(token))
        if preview is None or preview.status != "accepted":
            return None
        credentials = self.get_login_credentials(preview.subject)
        if credentials is None or credentials.mfa_secret is None:
            return None
        return verify_code(credentials.mfa_secret, totp_code)

    def create_api_key(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        *,
        name: str,
        scope: str,
        facility_ids: Sequence[uuid.UUID] = (),
    ) -> ApiKeySummary:
        """Creates a service `User` (no password/MFA -- unusable for
        interactive login) holding its own ordinary `role=api_service`
        `Membership`, then the `ApiKey` row pointing at it -- reusing the
        exact same resolution/RLS machinery a human user goes through
        rather than a parallel authorization system (`db.models.ApiKey`'s
        docstring). All three writes share one transaction via a single
        `access_session` block, same as `accept_invitation`'s multi-insert
        shape."""
        raw_key = generate_api_key()
        expires_at = datetime.now(UTC) + _API_KEY_TTL
        with access_session(self._session_factory, user_id) as session:
            service_user = db_repository.create_user(session, subject=f"api-key:{uuid.uuid4()}")
            db_repository.create_membership(
                session,
                service_user.id,
                org_id,
                role=Role.API_SERVICE.value,
                scope=scope,
                facility_ids=facility_ids,
            )
            row = db_repository.create_api_key(
                session,
                org_id,
                service_user.id,
                name=name,
                key_hash=hash_token(raw_key),
                created_by=user_id,
                expires_at=expires_at,
            )
        return ApiKeySummary(
            id=row.id,
            key=raw_key,
            name=row.name,
            scope=scope,
            facility_ids=tuple(facility_ids),
            expires_at=row.expires_at,
        )

    def list_api_keys(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, page: Page
    ) -> PagedResult[ApiKeyListItem]:
        with access_session(self._session_factory, user_id) as session:
            rows, total = db_repository.list_api_keys(
                session, org_id, limit=page.limit, offset=page.offset
            )
            items = [
                ApiKeyListItem(
                    id=row.id,
                    name=row.name,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    revoked_at=row.revoked_at,
                    last_used_at=row.last_used_at,
                )
                for row in rows
            ]
        return PagedResult(items=items, total=total, limit=page.limit, offset=page.offset)

    def revoke_api_key(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> bool:
        with access_session(self._session_factory, user_id) as session:
            return db_repository.revoke_api_key(session, api_key_id)

    def get_api_key_for_auth(self, raw_key: str) -> ApiKeyAuthLookup | None:
        with self._session_factory() as session:
            row = db_repository.get_api_key_by_hash(session, hash_token(raw_key))
        if row is None:
            return None
        return ApiKeyAuthLookup(
            id=row.id,
            org_id=row.org_id,
            user_id=row.user_id,
            name=row.name,
            revoked_at=row.revoked_at,
            expires_at=row.expires_at,
        )

    def touch_api_key_last_used(self, user_id: uuid.UUID, api_key_id: uuid.UUID) -> None:
        with access_session(self._session_factory, user_id) as session:
            db_repository.touch_api_key_last_used(session, api_key_id)

    def create_contract(
        self, user_id: uuid.UUID, org_id: uuid.UUID, *, payer_id: str, name: str
    ) -> ContractSummary:
        with access_session(self._session_factory, user_id) as session:
            row = db_repository.create_contract(session, org_id, payer_id, name)
            return ContractSummary(
                id=row.id, payer_id=row.payer_id, name=row.name, created_at=row.created_at
            )

    def create_contract_version(
        self,
        user_id: uuid.UUID,
        org_id: uuid.UUID,
        contract_id: uuid.UUID,
        data: ContractVersionInput,
    ) -> uuid.UUID:
        version = _rule_input_to_contract_version(data)
        with access_session(self._session_factory, user_id) as session:
            row = db_repository.create_contract_version(session, org_id, contract_id, version)
            return row.id

    def list_audit_log(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, *, filters: AuditLogFilters, page: Page
    ) -> PagedResult[AuditLogEntry]:
        with access_session(self._session_factory, user_id) as session:
            rows, total = db_repository.list_audit_log(
                session,
                facility_id,
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
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        phi_accessed: bool,
        request_id: str | None,
    ) -> None:
        with access_session(self._session_factory, user_id) as session:
            db_repository.write_audit_log(
                session,
                facility_id,
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                phi_accessed=phi_accessed,
                request_id=request_id,
            )

    def generate_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        generated_by: str,
    ) -> RecoveryPacketSummary | PacketGenerationFailed | None:
        with access_session(self._session_factory, user_id) as session:
            detail = db_repository.get_finding_detail(session, facility_id, finding_id)
            if detail is None:
                return None
            db_repository.write_phi_access_log(
                session,
                facility_id,
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
                    facility_id,
                    actor=generated_by,
                    action="packet_draft_rejected",
                    resource_type="finding",
                    resource_id=str(finding_id),
                    phi_accessed=False,
                )

            if not result.success or result.final_text is None:
                db_repository.write_audit_log(
                    session,
                    facility_id,
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
                facility_id,
                finding_id,
                draft_text=result.final_text,
                deadline=deadline,
                generated_by=generated_by,
            )
            db_repository.write_audit_log(
                session,
                facility_id,
                actor=generated_by,
                action="packet_generated",
                resource_type="recovery_packet",
                resource_id=str(row.id),
                phi_accessed=True,
            )
            return _packet_to_summary(row)

    def list_packets(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, finding_id: uuid.UUID, *, actor: str
    ) -> list[RecoveryPacketSummary]:
        with access_session(self._session_factory, user_id) as session:
            finding = session.get(FindingModel, finding_id)
            if finding is not None:
                db_repository.write_phi_access_log(
                    session,
                    facility_id,
                    actor=actor,
                    claim_id=finding.claim_id,
                    purpose="packet_list_view",
                )
                self._record_phi_access(actor)
            rows = db_repository.list_recovery_packets_for_finding(
                session, facility_id, finding_id
            )
            return [_packet_to_summary(row) for row in rows]

    def get_claim_access_history(
        self, user_id: uuid.UUID, facility_id: uuid.UUID, claim_id: uuid.UUID
    ) -> tuple[AccessEventSummary, ...]:
        with access_session(self._session_factory, user_id) as session:
            events = db_repository.get_claim_access_history(session, facility_id, claim_id)
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
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        finding_id: uuid.UUID,
        *,
        data: RecordOutcomeInput,
        recorded_by: str,
    ) -> FindingSummary | None:
        with access_session(self._session_factory, user_id) as session:
            row = session.get(FindingModel, finding_id)
            if row is None or row.facility_id != facility_id:
                return None
            existing_outcome = Outcome(row.outcome) if row.outcome is not None else None
            validate_outcome_recording(RootCause[row.root_cause], existing_outcome)
            updated = db_repository.record_finding_outcome(
                session,
                facility_id,
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
                facility_id,
                actor=recorded_by,
                action="finding_outcome_recorded",
                resource_type="finding",
                resource_id=str(finding_id),
                phi_accessed=True,
            )
            return _finding_to_summary(updated)

    def decide_packet(
        self,
        user_id: uuid.UUID,
        facility_id: uuid.UUID,
        packet_id: uuid.UUID,
        *,
        approve: bool,
        decided_by: str,
    ) -> RecoveryPacketSummary | None:
        with access_session(self._session_factory, user_id) as session:
            existing = session.get(RecoveryPacketModel, packet_id)
            if existing is None or existing.facility_id != facility_id:
                return None
            row = db_repository.decide_recovery_packet(
                session, facility_id, packet_id, approve=approve, decided_by=decided_by
            )
            db_repository.write_audit_log(
                session,
                facility_id,
                actor=decided_by,
                action="packet_approved" if approve else "packet_rejected",
                resource_type="recovery_packet",
                resource_id=str(packet_id),
                phi_accessed=False,
            )
            return _packet_to_summary(row)
