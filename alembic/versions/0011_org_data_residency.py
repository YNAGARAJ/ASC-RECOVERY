"""Add org_policies.data_residency_region -- Phase 6 (`docs/MASTER-
BUILD-PROMPT-V2.md`), the last item of the phase's checklist. A stored
declaration, not a technical control: this system runs one shared
Postgres instance in one region today, so there is no per-org routing
or physical placement this column drives. See `db/models.py`'s
`OrgPolicy.data_residency_region` docstring for the full reasoning, and
`docs/RUNBOOK.md`'s "Per-org data residency" section for the
operational meaning.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Same guard as 0002/0010/etc.: 0001's create_all() already creates
    # this column on a fresh database (it's part of OrgPolicy in
    # db/models.py by the time this migration is written) -- an
    # unconditional add_column here would fail with DuplicateColumn on
    # that path. Offline SQL generation has no live connection to
    # inspect, so always emit the statement there (for eyeballing SQL,
    # never for applying it).
    if context.is_offline_mode():
        op.add_column(
            "org_policies", sa.Column("data_residency_region", sa.String(100), nullable=True)
        )
        return

    inspector = inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("org_policies")}
    if "data_residency_region" not in existing:
        op.add_column(
            "org_policies", sa.Column("data_residency_region", sa.String(100), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("org_policies", "data_residency_region")
