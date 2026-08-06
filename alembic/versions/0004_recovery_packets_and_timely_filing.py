"""Phase 7 schema: `recovery_packets` (facility-scoped, RLS-enabled -- the
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
from alembic import context, op
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from db import models  # noqa: F401 -- registers RecoveryPacket on Base.metadata
from db.base import Base

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"


def _grant_and_secure_recovery_packets() -> None:
    # Idempotent statements only (GRANT and ENABLE/FORCE ROW LEVEL SECURITY
    # are no-ops on re-application in Postgres) plus CREATE POLICY, which
    # is NOT idempotent but is safe unguarded because Alembic guarantees
    # this migration's upgrade() runs at most once per database. This is
    # the *only* place recovery_packets gets any of the four -- 0001's
    # create_all() creates the table but its grant/RLS loops only know
    # about the tables hardcoded in `_MUTABLE_TABLES`/`_FACILITY_SCOPED_TABLES`
    # at the time 0001 was written, which predates this table. Policy
    # shape (Phase 4, `docs/MASTER-BUILD-PROMPT-V2.md`) matches every other
    # facility-scoped table -- see 0001's `resolve_accessible_facility_ids`.
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON recovery_packets TO {_APP_ROLE}"))
    op.execute(text("ALTER TABLE recovery_packets ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE recovery_packets FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY facility_access ON recovery_packets "
            "USING (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()

    # Offline SQL generation (`alembic upgrade head --sql`) has no live
    # connection to inspect -- always emit every statement there, matching
    # this migration's original, unconditional behavior; that path is for
    # eyeballing SQL, never for applying it.
    if context.is_offline_mode():
        op.add_column(
            "contracts",
            sa.Column("timely_filing_days", sa.Integer(), nullable=False, server_default="90"),
        )
        op.add_column("contracts", sa.Column("packet_template", JSONB(), nullable=True))
        Base.metadata.tables["recovery_packets"].create(bind=bind, checkfirst=False)
        _grant_and_secure_recovery_packets()
        return

    inspector = inspect(bind)

    # Guarded, same reason as 0002/0003: on a fresh database, 0001's
    # create_all() already creates these (they're part of db/models.py
    # now, even though it predates this migration).
    existing_contract_columns = {col["name"] for col in inspector.get_columns("contracts")}
    if "timely_filing_days" not in existing_contract_columns:
        op.add_column(
            "contracts",
            sa.Column("timely_filing_days", sa.Integer(), nullable=False, server_default="90"),
        )
    if "packet_template" not in existing_contract_columns:
        op.add_column("contracts", sa.Column("packet_template", JSONB(), nullable=True))

    if not inspector.has_table("recovery_packets"):
        Base.metadata.tables["recovery_packets"].create(bind=bind, checkfirst=False)

    _grant_and_secure_recovery_packets()


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS facility_access ON recovery_packets"))
    Base.metadata.tables["recovery_packets"].drop(bind=op.get_bind())
    op.drop_column("contracts", "packet_template")
    op.drop_column("contracts", "timely_filing_days")
