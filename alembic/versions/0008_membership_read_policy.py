"""Phase 5 step 2 (`docs/MASTER-BUILD-PROMPT-V2.md`): delegated-admin read
access on `memberships`/`membership_facilities`.

0007 added an org-resolved `org_authoring` INSERT/UPDATE policy on
`memberships` (an org-resolved caller can create/revoke *someone else's*
membership) but deliberately left read access at self-only -- the read
shape wasn't known until the "list org members" surface itself was
designed. That surface (`GET /organizations/members`,
`api/routes/organizations.py`) now exists, so this migration adds the
matching SELECT policy: an org-resolved caller can see every membership
row in their resolved org tree, not just their own. Same treatment for
`membership_facilities` (needed to show which facilities back a
SPECIFIC_FACILITIES-scoped member).

Both are additive -- Postgres OR's multiple permissive policies on the
same table for the same command, so the existing self-only SELECT
policies from 0007 are untouched, this just widens what's also visible.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            "CREATE POLICY org_authoring_select ON memberships "
            "FOR SELECT "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    op.execute(
        text(
            "CREATE POLICY org_authoring_select ON membership_facilities "
            "FOR SELECT "
            "USING (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS org_authoring_select ON membership_facilities"))
    op.execute(text("DROP POLICY IF EXISTS org_authoring_select ON memberships"))
