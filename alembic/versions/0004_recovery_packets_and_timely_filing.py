"""Phase 7 schema: `recovery_packets` (tenant-scoped, RLS-enabled -- the
human-approval state machine for LLM-drafted appeal letters) plus
`contracts.timely_filing_days` / `contracts.packet_template` (payer-level
appeal-window and letter-template attributes; deliberately not on
`contract_versions` -- see `src/db/models.py`'s `Contract` docstring).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from db import models  # noqa: F401 -- registers RecoveryPacket on Base.metadata
from db.base import Base

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "contracts",
        sa.Column("timely_filing_days", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column("contracts", sa.Column("packet_template", JSONB(), nullable=True))

    Base.metadata.tables["recovery_packets"].create(bind=bind, checkfirst=False)

    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON recovery_packets TO {_APP_ROLE}"))
    op.execute(text("ALTER TABLE recovery_packets ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE recovery_packets FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY tenant_isolation ON recovery_packets "
            "USING (tenant_id = current_setting('app.tenant_id')::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid)"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON recovery_packets"))
    Base.metadata.tables["recovery_packets"].drop(bind=op.get_bind())
    op.drop_column("contracts", "packet_template")
    op.drop_column("contracts", "timely_filing_days")
