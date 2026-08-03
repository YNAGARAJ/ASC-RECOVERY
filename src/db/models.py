"""SQLAlchemy 2.0 ORM models for the Phase 3 persistence layer.

Every PHI-bearing table carries `tenant_id`, non-null, foreign-keyed to
`tenants` -- per CLAUDE.md rule 8, there is no global read. The Alembic
migration (alembic/versions/0001_initial_schema.py) is what actually
enables Row-Level Security and creates the tenant-isolation policy; this
module only defines table shape.

Enum-like columns (claim status, root cause, pricing method, etc.) are
plain bounded strings, not native Postgres ENUM types -- adding a new
RootCause value would otherwise require an ALTER TYPE migration every time
domain.variance changes. Validity is enforced at the Python/domain layer,
which already has typed enums for all of these.

Rule sub-structures on contract_versions (MPPR, bilateral, assistant
surgeon, implant carve-out) are stored as JSONB rather than normalized into
four more tables -- they're small, nested, and never queried directly; the
repository layer is the single place that (de)serializes them into the
Phase 1 domain dataclasses.
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


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    payer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class ContractVersion(Base):
    __tablename__ = "contract_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_versions.id"), nullable=False
    )
    procedure_code: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class Remittance(Base):
    __tablename__ = "remittances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "file_hash", name="uq_remittance_tenant_file_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(200), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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
    # PHI, stored plain for now -- application-level column encryption is
    # Phase 4 scope (docs/MASTER-BUILD-PROMPT.md). Not yet safe for real PHI.
    patient_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    patient_member_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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


class AuditLog(Base):
    """Append-only. The Alembic migration revokes UPDATE/DELETE from asc_app
    on this table after granting INSERT, SELECT -- see
    alembic/versions/0001_initial_schema.py."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
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


class PHIAccessLog(Base):
    """Minimum-necessary access reporting, separate from the general audit
    trail -- see the phase prompt's audit-log requirements."""

    __tablename__ = "phi_access_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id"), nullable=False
    )
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
