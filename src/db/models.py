"""SQLAlchemy 2.0 ORM models for the organization/facility/membership
access model (Phase 4, `MASTER-BUILD-PROMPT-V2.md`).

Replaces the earlier flat `tenant_id`-per-table model outright (see
`alembic/versions/0001_initial_schema.py`'s module docstring for why this
is a clean-cut schema replacement rather than a data migration -- no real
customer/PHI data has ever existed in this system).

Every table that previously carried `tenant_id` now carries either
`facility_id` (claims and everything that hangs off a claim, plus
remittances/audit/PHI-access/recovery-packet rows -- one remittance file
still maps to exactly one facility, same denormalization shape as before)
or `org_id` (contracts/contract_versions/fee_schedule_lines -- payer
contracts are negotiated per organization, so one ASC_GROUP's facilities
share one rate card instead of each needing its own).

`organizations` is self-referencing (`parent_org_id`) to represent
PLATFORM -> BILLING_COMPANY/ASC_GROUP -> ASC hierarchies. Access is never
a bare id comparison: a user's `memberships` row grants a role within one
org (plus, by the recursive access rule, every descendant org), narrowed
to specific facilities only if `scope=SPECIFIC_FACILITIES` via
`membership_facilities`. See `alembic/versions/0001_initial_schema.py`'s
`resolve_accessible_facility_ids`/`resolve_accessible_org_ids` SQL
functions for how this is actually enforced at the database level -- this
module only defines table shape.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class Organization(Base):
    """Self-referencing hierarchy: PLATFORM -> BILLING_COMPANY | ASC_GROUP
    -> ASC. `parent_org_id` is nullable only for root nodes (a standalone
    ASC with no parent, or the one PLATFORM node). RLS-protected like any
    other resolved-access table (see the migration) -- readable only for
    orgs the connected user's memberships resolve to, walking descendants
    from their active org."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    parent_org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Phase 6 (docs/MASTER-BUILD-PROMPT-V2.md), BYOK-ready per-org
    # encryption: NULL means "no dedicated key -- encrypt this org's PHI
    # under the platform's default KEK" (EnvelopeEncryptor.encrypt's own
    # fallback when no kek_id is given). Non-NULL is a full KMS key
    # identifier (an AWS KMS key/alias ARN, or an Azure Key Vault key's
    # versioned URL) meaningful only once the app actually runs against a
    # real cloud KMS (security/kms_aws.py, security/kms_azure.py) --
    # EnvKMS, the default stopgap adapter, is deliberately single-key and
    # ignores this column entirely (security/kms_env.py's docstring). The
    # same column serves both a platform-provisioned dedicated key and a
    # genuinely customer-supplied one ("bring your own key") -- from this
    # schema's perspective they're identical, just a key id string.
    kms_key_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Facility(Base):
    """One physical ASC location. Every PHI-bearing table's `facility_id`
    points here; `org_id` (and therefore the org hierarchy) is derived by
    joining through this table, never denormalized further."""

    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class User(Base):
    """Deliberately ungated like Organization/Facility, not RLS-protected
    -- resolving a bearer token's `sub` claim to a user row is how a
    session gets bootstrapped in the first place (src/api/auth.py), so
    this lookup can't itself require `app.user_id` to already be set. No
    `tenant_id`/`facility_id`/`org_id` column here at all: a user's access
    is entirely defined by their `Membership` rows, not a column on this
    table -- the same person can hold different roles in different orgs."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Both nullable: a user row can exist (e.g. provisioned by an admin)
    # before credentials are set up. api/routes/auth.py's login route
    # treats a NULL password_hash or mfa_secret_encrypted the same as a
    # wrong password/code -- there is no partial-credential login. See
    # security/passwords.py for the hash format and security/mfa.py's
    # docstring for why the TOTP secret must be encrypted at rest.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(Base):
    """One role, held by one user, within one org (plus every descendant
    org, by the recursive access rule). `scope=ALL_FACILITIES` grants
    every facility under the org tree rooted at `org_id`;
    `scope=SPECIFIC_FACILITIES` narrows to whatever's listed in
    `MembershipFacility`. RLS-protected by a bootstrap-safe *self-only*
    policy (`user_id = current_setting('app.user_id')`) -- not the
    recursive resolution function, since resolving that function's own
    org-hierarchy walk must not itself depend on a membership row already
    being visible. Phase 5 (`alembic/versions/0007_user_lifecycle.py`)
    adds a second, *additive* policy granting an org-resolved caller
    INSERT/UPDATE on someone else's membership row (delegated admin,
    offboarding, invitation acceptance, API-key provisioning) -- Postgres
    combines multiple permissive policies on the same table with OR, so
    this is layered on top of the self-only policy, not a replacement.

    `revoked_at` (Phase 5) is a soft revocation, not a `DELETE` -- asc_app
    has no `DELETE` grant on this table (retention posture, same as every
    other mutable table). Offboarding sets it via an `UPDATE`, which *is*
    granted. RLS visibility of a revoked row is unchanged (still visible
    for history to its own user / a resolved org admin) -- only the
    access-resolution functions (`resolve_accessible_facility_ids`/
    `resolve_accessible_org_ids`/`resolve_membership_role`) stop counting
    a revoked membership toward accessible facilities/orgs/role, which is
    what makes revocation take effect on the very next query."""

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipFacility(Base):
    """Narrows a `scope=SPECIFIC_FACILITIES` membership to specific
    facilities. No surrogate id -- the pair is the identity."""

    __tablename__ = "membership_facilities"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memberships.id"), primary_key=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), primary_key=True
    )


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    payer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # A timely-filing window and letter boilerplate are payer-relationship
    # attributes, not effective-dated pricing rules -- they live here
    # rather than on ContractVersion (domain.contract.ContractVersion is a
    # frozen dataclass with factories across tests/domain, tests/ingestion,
    # tests/api; adding a field there ripples everywhere for no benefit,
    # since neither of these needs to vary by contract version the way a
    # fee schedule does).
    timely_filing_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("90")
    )
    packet_template: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ContractVersion(Base):
    __tablename__ = "contract_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    default_pricing_method: Mapped[str] = mapped_column(String(30), nullable=False)
    percent_of_charge_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    mppr_rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    bilateral_rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    assistant_surgeon_rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    implant_carveout_rule: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    case_rate_groups: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class FeeScheduleLine(Base):
    __tablename__ = "fee_schedule_lines"
    __table_args__ = (
        UniqueConstraint("contract_version_id", "procedure_code", name="uq_fee_schedule_line_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_versions.id"), nullable=False
    )
    procedure_code: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Remittance(Base):
    __tablename__ = "remittances"
    __table_args__ = (
        UniqueConstraint("facility_id", "file_hash", name="uq_remittance_facility_file_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    remittance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("remittances.id"), nullable=False
    )
    patient_control_number: Mapped[str] = mapped_column(String(50), nullable=False)
    payer_claim_control_number: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    date_of_service: Mapped[date] = mapped_column(Date, nullable=False)
    total_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_paid_reported: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    patient_responsibility: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # AES-256-GCM envelope-encrypted (security.encryption.EnvelopeEncryptor)
    # before this column is ever written -- see security.phi_columns for the
    # JSON serialization format and ingestion.apply for the write path.
    # Never store patient name/member id in plaintext here. Also subject to
    # field-level PHI masking at read time for roles lacking the unmasked-
    # PHI permission (security/phi_masking.py) -- a separate mechanism from
    # encryption-at-rest, enforced in the repository query layer.
    patient_name_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_member_id_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceLine(Base):
    __tablename__ = "service_lines"
    __table_args__ = (
        UniqueConstraint("claim_id", "line_index", name="uq_service_line_claim_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    procedure_code: Mapped[str] = mapped_column(String(20), nullable=False)
    modifiers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    revenue_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    allowed: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_computed: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    service_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Adjustment(Base):
    __tablename__ = "adjustments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    service_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_lines.id"), nullable=True
    )
    group_code: Mapped[str] = mapped_column(String(2), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    service_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_lines.id"), nullable=False
    )
    contract_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_versions.id"), nullable=True
    )
    line_index: Mapped[int] = mapped_column(Integer, nullable=False)
    procedure_code: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_allowed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_allowed: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shortfall: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    root_cause: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    # Phase 12's outcome feedback loop -- recorded once, by a human, never
    # silently overwritten (domain.outcomes.validate_outcome_recording
    # enforces this before any write). NULL until an outcome is recorded.
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_recovered: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    outcome_recorded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditLog(Base):
    """Append-only. The Alembic migration revokes UPDATE/DELETE from asc_app
    on this table after granting INSERT, SELECT -- see
    alembic/versions/0001_initial_schema.py."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    phi_accessed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PHIAccessLog(Base):
    """Minimum-necessary access reporting, separate from the general audit
    trail -- see the phase prompt's audit-log requirements."""

    __tablename__ = "phi_access_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)


class RecoveryPacket(Base):
    """The human-approval state machine for Phase 7's LLM-drafted appeal
    letters: draft -> approved | rejected. `draft_text` is the fully
    rendered letter with the patient's identifying details substituted
    back in after generation (an appeal letter the payer can act on needs
    them) -- the LLM itself only ever saw placeholder tokens, never those
    details; see packets.prompt. There is deliberately no "sent" status
    or transmission mechanism here -- out of Phase 7's scope."""

    __tablename__ = "recovery_packets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    # Holds whoever made the approve-or-reject call, and when -- not named
    # "approved_by" since status can land on either "approved" or
    # "rejected".
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invitation(Base):
    """Phase 5 (`docs/MASTER-BUILD-PROMPT-V2.md`) user lifecycle: an
    org_admin (or higher) invites `subject` (an email address) to join
    `org_id` with `role`/`scope`. `token_hash` is a SHA-256 hash of a
    random token shown to the inviter exactly once (in the API response
    -- there is no email-sending infrastructure in this codebase, see
    `docs/RUNBOOK.md`); only the hash is ever persisted, matching
    `security/passwords.py`'s never-store-the-raw-credential discipline.
    `status` moves pending -> accepted (via `accept_invitation`, a
    `SECURITY DEFINER` function -- see `alembic/versions/0007_user_lifecycle.py`)
    or -> expired/revoked; never back. RLS-protected the same way
    `contracts` is (org-scoped, via `resolve_accessible_org_ids`) for the
    org_admin-facing list/manage surface -- the anonymous accept flow
    reaches this table only through the `SECURITY DEFINER` function,
    which bypasses RLS by design (token possession is the authorization,
    the same principle as a password-reset link)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvitationFacility(Base):
    """Narrows a `scope=SPECIFIC_FACILITIES` invitation to specific
    facilities -- mirrors `MembershipFacility` exactly, and is copied
    into a real `MembershipFacility` row by `accept_invitation` once the
    invitation is accepted."""

    __tablename__ = "invitation_facilities"

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invitations.id"), primary_key=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), primary_key=True
    )


class ApiKey(Base):
    """Phase 5: a scoped, rotatable, expiring credential for
    machine-to-machine access -- never interactive (no login/MFA flow).
    Deliberately does not duplicate role/scope/facility columns:
    `user_id` points at a service-user row (a `User` with no
    `password_hash`/`mfa_secret_encrypted`, matching how the login route
    already treats a NULL credential as unusable for interactive login)
    that holds its own ordinary `role=api_service` `Membership` --
    reusing the exact same resolution/RLS machinery a human user goes
    through, rather than a parallel authorization system to keep in
    sync. `org_id` is denormalized from that membership purely for RLS
    scoping, same reasoning as `facility_id`/`org_id` on every other
    table in this schema (see `db/models.py`'s module docstring) -- RLS
    filters directly on it rather than joining through `user_id` ->
    `memberships` at policy-evaluation time. `key_hash` is a SHA-256
    hash of the raw key, shown to the creator exactly once in the API
    response, same discipline as `Invitation.token_hash`."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgPolicy(Base):
    """Phase 5 per-org security policy. One row per organization, created
    lazily (a missing row means "use the application defaults" --
    `ACCESS_TOKEN_TTL`/`REFRESH_TOKEN_TTL` from `security/session.py`, no
    IP restriction).

    `mfa_required` exists to satisfy the phase spec's literal request for
    a per-org MFA policy, but -- by explicit product decision, confirmed
    2026-08-07 -- the API never accepts `false` for it. MFA is mandatory
    with no exceptions (CLAUDE.md rule; `security/session.py::issue_session`
    already refuses to mint a session without it) and this column does
    not, and must never, carve out a per-org bypass of that rule. It is
    reserved for a possible future *stricter*-than-default policy, not a
    weaker one."""

    __tablename__ = "org_policies"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), primary_key=True
    )
    session_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mfa_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    ip_allowlist: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
