"""Add `jobs` -- Phase 7 (`docs/MASTER-BUILD-PROMPT-V2.md`) async job
queue, Postgres-backed (`SELECT ... FOR UPDATE SKIP LOCKED`, `src/jobs/`).
Facility-scoped like every other PHI-adjacent table, same
`facility_access` RLS policy shape 0001's `_secure_facility_scoped`
established for `claims`/`findings`/etc. -- see `db/models.py`'s `Job`
docstring for the full schema reasoning.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
from sqlalchemy import inspect, text

from db import models  # noqa: F401 -- registers Job on Base.metadata
from db.base import Base

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"
_TABLE = "jobs"


def _create_table_if_missing() -> None:
    # Same guard as every migration since 0002: 0001's create_all() already
    # creates this table on a fresh database (it's part of Base.metadata by
    # the time this migration is written) -- an unconditional create_table
    # here would fail with DuplicateTable on that path. Offline SQL
    # generation has no live connection to inspect, so always emit the
    # statement there (for eyeballing SQL, never for applying it).
    if context.is_offline_mode():
        Base.metadata.tables[_TABLE].create(bind=op.get_bind(), checkfirst=False)
        return

    inspector = inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        Base.metadata.tables[_TABLE].create(bind=op.get_bind(), checkfirst=False)


def _secure_table() -> None:
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO {_APP_ROLE}"))
    op.execute(text(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY facility_access ON jobs "
            "USING (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def upgrade() -> None:
    _create_table_if_missing()
    _secure_table()


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS facility_access ON jobs"))
    Base.metadata.tables[_TABLE].drop(bind=op.get_bind())
