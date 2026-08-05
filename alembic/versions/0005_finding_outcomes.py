"""Add findings.outcome/amount_recovered/outcome_recorded_by/
outcome_recorded_at -- Phase 12's outcome feedback loop needs somewhere to
record what actually happened to a finding (recovered, denied, abandoned,
expired) so a future finding for the same payer and root cause can carry
a real, earned confidence score instead of a guess.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NEW_COLUMNS = (
    sa.Column("outcome", sa.String(length=20), nullable=True),
    sa.Column("amount_recovered", sa.Numeric(12, 2), nullable=True),
    sa.Column("outcome_recorded_by", sa.String(length=200), nullable=True),
    sa.Column("outcome_recorded_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    # Guarded, same reason as 0002/0003/0004: migration 0001 builds its
    # schema via `Base.metadata.create_all()` against whatever's
    # *currently* in db/models.py, so on a fresh database it already
    # creates these columns (they're part of the `Finding` model as of
    # Phase 12) and an unconditional add_column here would fail with
    # DuplicateColumn.
    #
    # Offline SQL generation has no live connection to inspect -- always
    # emit every statement there, matching every prior migration's
    # offline branch.
    if context.is_offline_mode():
        for column in _NEW_COLUMNS:
            op.add_column("findings", column)
        return

    inspector = inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("findings")}
    for column in _NEW_COLUMNS:
        if column.name not in existing:
            op.add_column("findings", column)


def downgrade() -> None:
    op.drop_column("findings", "outcome_recorded_at")
    op.drop_column("findings", "outcome_recorded_by")
    op.drop_column("findings", "amount_recovered")
    op.drop_column("findings", "outcome")
