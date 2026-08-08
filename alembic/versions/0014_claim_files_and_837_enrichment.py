"""Phase 9 schema (`docs/MASTER-BUILD-PROMPT-V2.md`): 837 claim file
ingestion. New `claim_files` table (facility-scoped, same RLS shape as
every table since 0001 -- see `db/models.py`'s `ClaimFile` docstring for
why it's a separate table from `remittances`, not a shared one with a
type discriminator). `claims` gains `rendering_provider_name` (plain
text -- already parsed off the 835 today, previously dropped before
persistence) and `diagnosis_codes_encrypted` (PHI, envelope-encrypted,
837-only source). `service_lines` gains `units` (already parsed off the
835 today, previously dropped).

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect, text

from db import models  # noqa: F401 -- registers ClaimFile on Base.metadata
from db.base import Base

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"
_CLAIM_FILES_TABLE = "claim_files"


def _create_claim_files_table_if_missing() -> None:
    # Same guard as every migration since 0002 -- 0001's create_all()
    # already creates this table on a fresh database (it's part of
    # db/models.py by the time this migration is written). Offline SQL
    # generation has no live connection to inspect, so always emit the
    # statement there (for eyeballing SQL, never for applying it).
    if context.is_offline_mode():
        Base.metadata.tables[_CLAIM_FILES_TABLE].create(bind=op.get_bind(), checkfirst=False)
        return

    inspector = inspect(op.get_bind())
    if not inspector.has_table(_CLAIM_FILES_TABLE):
        Base.metadata.tables[_CLAIM_FILES_TABLE].create(bind=op.get_bind(), checkfirst=False)


def _secure_claim_files_table() -> None:
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {_CLAIM_FILES_TABLE} TO {_APP_ROLE}"))
    op.execute(text(f"ALTER TABLE {_CLAIM_FILES_TABLE} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {_CLAIM_FILES_TABLE} FORCE ROW LEVEL SECURITY"))
    # Table name hardcoded directly, not f-string-interpolated, matching
    # every prior migration's own CREATE POLICY statement (0004, 0012) --
    # bandit's B608 heuristic flags a SELECT-containing f-string
    # regardless of whether the interpolated value is a hardcoded
    # module-level constant (it is, `_CLAIM_FILES_TABLE`, never user
    # input) or not.
    op.execute(
        text(
            "CREATE POLICY facility_access ON claim_files "
            "USING (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _add_claim_and_service_line_columns() -> None:
    if context.is_offline_mode():
        op.add_column("claims", sa.Column("rendering_provider_name", sa.String(200), nullable=True))
        op.add_column("claims", sa.Column("diagnosis_codes_encrypted", sa.Text(), nullable=True))
        op.add_column("service_lines", sa.Column("units", sa.Numeric(9, 2), nullable=True))
        return

    inspector = inspect(op.get_bind())
    existing_claim_columns = {col["name"] for col in inspector.get_columns("claims")}
    if "rendering_provider_name" not in existing_claim_columns:
        op.add_column("claims", sa.Column("rendering_provider_name", sa.String(200), nullable=True))
    if "diagnosis_codes_encrypted" not in existing_claim_columns:
        op.add_column("claims", sa.Column("diagnosis_codes_encrypted", sa.Text(), nullable=True))

    existing_service_line_columns = {col["name"] for col in inspector.get_columns("service_lines")}
    if "units" not in existing_service_line_columns:
        op.add_column("service_lines", sa.Column("units", sa.Numeric(9, 2), nullable=True))


def upgrade() -> None:
    _create_claim_files_table_if_missing()
    _secure_claim_files_table()
    _add_claim_and_service_line_columns()


def downgrade() -> None:
    op.drop_column("service_lines", "units")
    op.drop_column("claims", "diagnosis_codes_encrypted")
    op.drop_column("claims", "rendering_provider_name")
    op.execute(text(f"DROP POLICY IF EXISTS facility_access ON {_CLAIM_FILES_TABLE}"))
    Base.metadata.tables[_CLAIM_FILES_TABLE].drop(bind=op.get_bind())
