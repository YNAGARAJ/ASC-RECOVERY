"""Add `users` (ungated, like `organizations`/`facilities` -- see
src/db/models.py's User docstring for why) and `audit_log.request_id`,
needed for Phase 6's API layer: resolving a bearer token's subject to a
user id requires a lookup that can't itself be access-scoped, and
"request ID propagated into every log line and audit entry"
(docs/MASTER-BUILD-PROMPT.md, Phase 6) needs somewhere on `audit_log` to
put it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect, text

from db import models  # noqa: F401 -- registers User on Base.metadata
from db.base import Base

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"


def upgrade() -> None:
    # Offline SQL generation (`alembic upgrade head --sql`) has no live
    # connection to inspect -- always emit every statement there, matching
    # this migration's original, unconditional behavior; that path is for
    # eyeballing SQL, never for applying it.
    if context.is_offline_mode():
        Base.metadata.tables["users"].create(bind=op.get_bind(), checkfirst=False)
        op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON users TO {_APP_ROLE}"))
        op.add_column("audit_log", sa.Column("request_id", sa.String(length=36), nullable=True))
        return

    bind = op.get_bind()
    inspector = inspect(bind)

    # Guarded creation: on a fresh database, 0001's create_all() already
    # creates this table (it's part of Base.metadata now, even though
    # 0001's own hardcoded grant/RLS table lists predate it -- see below).
    # checkfirst=False here would fail with "table already exists".
    if not inspector.has_table("users"):
        Base.metadata.tables["users"].create(bind=bind, checkfirst=False)
    # Not guarded: GRANT is idempotent in Postgres (re-granting an
    # already-held privilege is a no-op, never an error), and this
    # statement is the *only* place `users` gets its asc_app grant --
    # 0001's create_all() creates the table but its GRANT loop only knows
    # about the tables hardcoded in `_MUTABLE_TABLES` at the time 0001 was
    # written, which predates this table. Must always run regardless of
    # whether the table above was just created or already existed.
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON users TO {_APP_ROLE}"))

    existing_audit_log_columns = {col["name"] for col in inspector.get_columns("audit_log")}
    if "request_id" not in existing_audit_log_columns:
        op.add_column("audit_log", sa.Column("request_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "request_id")
    Base.metadata.tables["users"].drop(bind=op.get_bind())
