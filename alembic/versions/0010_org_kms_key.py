"""Add organizations.kms_key_id -- Phase 6 (`docs/MASTER-BUILD-PROMPT-V2.md`)
per-org, BYOK-ready encryption keys. NULL means "use the platform's
default KEK" (`EnvelopeEncryptor.encrypt`'s own fallback); non-NULL is a
full KMS key identifier, meaningful once the app runs against a real
cloud KMS (`security/kms_aws.py`, `security/kms_azure.py`) -- see
`db/models.py`'s `Organization.kms_key_id` docstring for the full
reasoning, including why `EnvKMS` (the default stopgap adapter)
deliberately ignores this column.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Same guard as 0002/0003/etc.: 0001's create_all() already creates
    # this column on a fresh database (it's part of Organization in
    # db/models.py by the time this migration is written) -- an
    # unconditional add_column here would fail with DuplicateColumn on
    # that path. Offline SQL generation has no live connection to
    # inspect, so always emit the statement there (for eyeballing SQL,
    # never for applying it).
    if context.is_offline_mode():
        op.add_column("organizations", sa.Column("kms_key_id", sa.String(500), nullable=True))
        return

    inspector = inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("organizations")}
    if "kms_key_id" not in existing:
        op.add_column("organizations", sa.Column("kms_key_id", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "kms_key_id")
