"""Phase 8 schema (`docs/MASTER-BUILD-PROMPT-V2.md`): `contract_versions`
gains `lesser_of_charge_enabled` (the literal false-positive fix -- "pay
the lesser of billed charges or the fee schedule") and `stop_loss_rule`
(outlier/stop-loss pricing above a claim-level charge threshold). First
real `ALTER TABLE contract_versions` since the table's creation -- see
`src/domain/contract.py`'s `ContractVersion`/`StopLossRule` docstrings for
the pricing rationale.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TABLE = "contract_versions"
_STOP_LOSS_DEFAULT = (
    '\'{"enabled": false, "threshold": "0", "outlier_rate": "0", '
    '"first_dollar": true}\'::jsonb'
)


def upgrade() -> None:
    # Offline SQL generation has no live connection to inspect -- always
    # emit both statements there, same as every migration's offline
    # branch (see 0004's own comment on this).
    if context.is_offline_mode():
        op.add_column(
            _TABLE,
            sa.Column(
                "lesser_of_charge_enabled",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
        op.add_column(
            _TABLE,
            sa.Column(
                "stop_loss_rule",
                JSONB(),
                nullable=False,
                server_default=sa.text(_STOP_LOSS_DEFAULT),
            ),
        )
        return

    # Guarded, same reason as 0004: a fresh database's 0001 create_all()
    # already creates these columns (they're part of db/models.py by the
    # time this migration is written) -- an unconditional add_column here
    # would fail with DuplicateColumn on that path.
    inspector = inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns(_TABLE)}
    if "lesser_of_charge_enabled" not in existing_columns:
        op.add_column(
            _TABLE,
            sa.Column(
                "lesser_of_charge_enabled",
                sa.Boolean(),
                nullable=False,
                server_default="true",
            ),
        )
    if "stop_loss_rule" not in existing_columns:
        op.add_column(
            _TABLE,
            sa.Column(
                "stop_loss_rule",
                JSONB(),
                nullable=False,
                server_default=sa.text(_STOP_LOSS_DEFAULT),
            ),
        )


def downgrade() -> None:
    op.drop_column(_TABLE, "stop_loss_rule")
    op.drop_column(_TABLE, "lesser_of_charge_enabled")
