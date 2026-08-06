"""Add users.password_hash / users.mfa_secret_encrypted -- F-04/F-05
(docs/audit/REGISTER.md): no login endpoint existed anywhere, and even
once one is built, the users table has nowhere to store a password or an
encrypted TOTP secret to check it against. Both columns are nullable: a
user row can exist before credentials are provisioned, and
api/routes/auth.py's login route treats a NULL value the same as a wrong
password/code -- there is no partial-credential login path.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_NEW_COLUMNS = (
    sa.Column("password_hash", sa.String(length=255), nullable=True),
    sa.Column("mfa_secret_encrypted", sa.Text(), nullable=True),
)


def upgrade() -> None:
    # Guarded, same reason as 0002/0003/0004/0005: migration 0001 builds
    # its schema via `Base.metadata.create_all()` against whatever's
    # *currently* in db/models.py, so on a fresh database it already
    # creates these columns (they're part of the `User` model as of this
    # migration) and an unconditional add_column here would fail with
    # DuplicateColumn.
    #
    # Offline SQL generation has no live connection to inspect -- always
    # emit every statement there, matching every prior migration's
    # offline branch.
    if context.is_offline_mode():
        for column in _NEW_COLUMNS:
            op.add_column("users", column)
        return

    inspector = inspect(op.get_bind())
    existing = {col["name"] for col in inspector.get_columns("users")}
    for column in _NEW_COLUMNS:
        if column.name not in existing:
            op.add_column("users", column)


def downgrade() -> None:
    op.drop_column("users", "mfa_secret_encrypted")
    op.drop_column("users", "password_hash")
