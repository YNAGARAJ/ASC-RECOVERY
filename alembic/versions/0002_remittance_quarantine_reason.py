"""Add remittances.quarantine_reason -- Phase 5 ingestion needs somewhere to
store a human-readable diagnostic when a file is quarantined (unparseable,
undecodable, or virus-flagged) so "quarantined with a useful message" is
actually queryable, not just implied by status="quarantined".

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Guarded, not a plain add_column: migration 0001 builds its schema via
    # `Base.metadata.create_all()` against whatever's *currently* in
    # db/models.py, not a historical snapshot from when 0001 was written --
    # so on a fresh database, 0001 already creates this column (it's been
    # part of the `Remittance` model since Phase 5), and an unconditional
    # add_column here fails with DuplicateColumn. Only ever a real gap on a
    # database that somehow has 0001 applied from before this column
    # existed in db/models.py -- not possible for this project's history,
    # but the guard costs nothing and makes the assumption explicit instead
    # of silently relying on migration-application order.
    #
    # Offline SQL generation (`alembic upgrade head --sql`) has no live
    # connection to inspect -- `inspect(op.get_bind())` raises
    # NoInspectionAvailable against its mock connection. That path is for
    # eyeballing SQL, never for applying it, so just always emit the
    # statement there, matching this migration's original behavior.
    if context.is_offline_mode():
        op.add_column("remittances", sa.Column("quarantine_reason", sa.Text(), nullable=True))
        return

    inspector = inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("remittances")}
    if "quarantine_reason" not in existing:
        op.add_column("remittances", sa.Column("quarantine_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("remittances", "quarantine_reason")
