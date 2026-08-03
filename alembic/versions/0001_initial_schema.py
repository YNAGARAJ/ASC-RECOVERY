"""Initial schema: tenants, effective-dated contracts, remittances, claims,
findings, append-only audit log -- with Row-Level Security as the tenant
isolation enforcement mechanism.

Runs as the table-owning role (asc_owner in local dev, see
docs/DB_SETUP.md). Table shape comes from src/db/models.py via
`Base.metadata.create_all` -- duplicating every column as an explicit
`op.create_table` call here would drift from the ORM models over time; the
security-critical parts (RLS, policies, grants, the audit_log append-only
restriction) are written out explicitly below because that is exactly
where hand-authored control matters.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from db import models  # noqa: F401 -- registers all tables on Base.metadata
from db.base import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Every table except `tenants` itself carries tenant_id and gets RLS.
_TENANT_SCOPED_TABLES = (
    "contracts",
    "contract_versions",
    "fee_schedule_lines",
    "remittances",
    "claims",
    "service_lines",
    "adjustments",
    "findings",
    "audit_log",
    "phi_access_log",
)

# Append-only: no UPDATE/DELETE grant for the application role, ever.
_APPEND_ONLY_TABLES = ("audit_log", "phi_access_log")

# Mutable operational tables: SELECT/INSERT/UPDATE, never DELETE -- retention
# is enforced by soft-delete (`deleted_at`) columns where applicable, per
# the 6-year HIPAA documentation requirement.
_MUTABLE_TABLES = (
    "contracts",
    "contract_versions",
    "fee_schedule_lines",
    "remittances",
    "claims",
    "service_lines",
    "adjustments",
    "findings",
)

_APP_ROLE = "asc_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=False)

    op.execute(text(f"GRANT SELECT, INSERT ON tenants TO {_APP_ROLE}"))

    for table in _MUTABLE_TABLES:
        op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {_APP_ROLE}"))

    for table in _APPEND_ONLY_TABLES:
        op.execute(text(f"GRANT SELECT, INSERT ON {table} TO {_APP_ROLE}"))
        op.execute(text(f"REVOKE UPDATE, DELETE ON {table} FROM {_APP_ROLE}"))

    for table in _TENANT_SCOPED_TABLES:
        op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        # FORCE matters even though asc_app isn't the table owner here --
        # cheap insurance against a future connection-role change silently
        # disabling the policy for whoever ends up owning these tables.
        op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            text(
                f"CREATE POLICY tenant_isolation ON {table} "
                "USING (tenant_id = current_setting('app.tenant_id')::uuid) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TENANT_SCOPED_TABLES):
        op.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    Base.metadata.drop_all(bind=bind)
