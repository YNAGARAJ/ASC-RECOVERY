"""Phase 5 step 5 (`docs/MASTER-BUILD-PROMPT-V2.md`): API key authentication.

`api_keys` (schema already created in 0007) has RLS enabled and forced,
scoped by the `org_access` policy to the *caller's* resolved org access --
correct for an already-authenticated admin creating/listing/revoking a
key, but useless for the one place that actually needs to read this table
with no caller identity yet at all: `api/auth.py`'s bootstrap step, which
must turn a presented API key into a `user_id`/`org_id` *before* any
`app.user_id` exists to resolve access from. Same bootstrap problem
0007's `get_invitation_by_token_hash` solves for anonymous invitation
lookups, same fix: a narrow `SECURITY DEFINER` function that bypasses RLS
for exactly this one read, keyed by the key's hash (never the raw key --
callers hash before calling, same discipline as every other token lookup
in this codebase).

No further schema change needed for create/list/revoke -- 0007's
`org_access` policy on `api_keys` already covers all three as ordinary
org-resolved reads/writes by an authenticated caller.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"

_GET_API_KEY_BY_HASH_SQL = """
CREATE FUNCTION get_api_key_by_hash(p_key_hash text)
RETURNS TABLE(
    id uuid, org_id uuid, user_id uuid, name text,
    revoked_at timestamptz, expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT k.id, k.org_id, k.user_id, k.name, k.revoked_at, k.expires_at
    FROM api_keys k
    WHERE k.key_hash = p_key_hash;
$$;
"""


def upgrade() -> None:
    op.execute(text(_GET_API_KEY_BY_HASH_SQL))
    op.execute(text("REVOKE ALL ON FUNCTION get_api_key_by_hash(text) FROM PUBLIC"))
    op.execute(text(f"GRANT EXECUTE ON FUNCTION get_api_key_by_hash(text) TO {_APP_ROLE}"))


def downgrade() -> None:
    op.execute(text("DROP FUNCTION IF EXISTS get_api_key_by_hash(text)"))
