"""Add `users` (ungated, like `tenants` -- see src/db/models.py's User
docstring for why) and `audit_log.request_id`, needed for Phase 6's API
layer: resolving a bearer token's subject to a tenant_id requires a lookup
that can't itself be tenant-scoped, and "request ID propagated into every
log line and audit entry" (docs/MASTER-BUILD-PROMPT.md, Phase 6) needs
somewhere on `audit_log` to put it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from db import models  # noqa: F401 -- registers User on Base.metadata
from db.base import Base

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["users"].create(bind=bind, checkfirst=False)
    # Same grant shape as `tenants` in 0001: plain SELECT/INSERT/UPDATE,
    # no RLS -- this table is queried before app.tenant_id is known.
    op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON users TO {_APP_ROLE}"))

    op.add_column("audit_log", sa.Column("request_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "request_id")
    Base.metadata.tables["users"].drop(bind=op.get_bind())
