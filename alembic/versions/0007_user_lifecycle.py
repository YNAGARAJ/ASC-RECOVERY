"""Phase 5 (`docs/MASTER-BUILD-PROMPT-V2.md`) user lifecycle: invitations,
API keys, per-org policy, and soft membership revocation.

The first migration to land *after* Phase 4's 0001 rather than another
edit to it -- 0001's schema is now sealed, this layers on top the same
way 0002-0006 already layer on 0001. Table/column creation below is
guarded the same way 0002-0006 are: `Base.metadata.create_all()` inside
0001's own `upgrade()` already creates every table/column *currently* in
`db/models.py` on a fresh database (this migration's tables and
`memberships.revoked_at` included, since they're part of the models by
the time this migration is written) -- an unconditional `add_column`/
`create_table` here would fail with `DuplicateColumn`/`DuplicateTable`
on that path. The RLS/grant/function statements below are *not* guarded
(same reasoning as 0004's `_grant_and_secure_recovery_packets`): Alembic
guarantees this migration's `upgrade()` runs at most once per database.

**Two `SECURITY DEFINER` functions, same `BYPASSRLS`-on-`asc_owner`
precondition as 0001's `resolve_accessible_facility_ids`/
`resolve_accessible_org_ids` (see that migration's own docstring):**
`get_invitation_by_token_hash` and `accept_invitation`. Both exist
because invitation *acceptance* has no authenticated caller at all -- the
invitee has no session yet, no `app.user_id`, and isn't the org_admin who
sent the invite. Neither the self-only nor the new org-authoring
`memberships` policy below can cover "an anonymous visitor creates their
own user+membership row from a valid, unexpired invitation token" --
token possession is the authorization here, the same principle as a
password-reset link, so these two functions bypass RLS by design, the
same narrow, justified way the Phase 4 resolution functions do.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect, text

from db import models  # noqa: F401 -- registers Invitation/ApiKey/OrgPolicy on Base.metadata
from db.base import Base

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_APP_ROLE = "asc_app"

_NEW_TABLES = ("invitations", "invitation_facilities", "api_keys", "org_policies")

# Same recursive walk as 0001's _ORG_TREE_CTE, now excluding revoked
# memberships from the base case -- this is what makes offboarding
# (an UPDATE setting memberships.revoked_at, not a DELETE asc_app isn't
# granted) take effect on the very next query. CREATE OR REPLACE keeps
# the existing GRANT EXECUTE from 0001 intact (Postgres preserves grants
# across a same-signature function replacement).
_ORG_TREE_CTE = """
    WITH RECURSIVE org_tree AS (
        SELECT o.id, ARRAY[o.id] AS visited
        FROM memberships m
        JOIN organizations o ON o.id = m.org_id
        WHERE m.user_id = p_user_id{scope_filter} AND m.revoked_at IS NULL
        UNION ALL
        SELECT child.id, org_tree.visited || child.id
        FROM organizations child
        JOIN org_tree ON child.parent_org_id = org_tree.id
        WHERE NOT (child.id = ANY(org_tree.visited))
    )
"""

_RESOLVE_FACILITY_IDS_SQL = """
CREATE OR REPLACE FUNCTION resolve_accessible_facility_ids(p_user_id uuid)
RETURNS TABLE(facility_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    {org_tree_cte}
    SELECT DISTINCT f.id
    FROM facilities f
    JOIN org_tree ot ON f.org_id = ot.id
    UNION
    SELECT mf.facility_id
    FROM memberships m
    JOIN membership_facilities mf ON mf.membership_id = m.id
    WHERE m.user_id = p_user_id AND m.scope = 'SPECIFIC_FACILITIES' AND m.revoked_at IS NULL;
$$;
"""

_RESOLVE_ORG_IDS_SQL = """
CREATE OR REPLACE FUNCTION resolve_accessible_org_ids(p_user_id uuid)
RETURNS TABLE(org_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    {org_tree_cte}
    SELECT DISTINCT id FROM org_tree;
$$;
"""

_LOOKUP_INVITATION_BY_HASH_SQL = """
CREATE FUNCTION get_invitation_by_token_hash(p_token_hash text)
RETURNS TABLE(
    id uuid, org_id uuid, subject text, role text, scope text,
    status text, expires_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT i.id, i.org_id, i.subject, i.role, i.scope, i.status, i.expires_at
    FROM invitations i
    WHERE i.token_hash = p_token_hash;
$$;
"""

# LANGUAGE plpgsql (not sql, unlike the resolution functions above) --
# this needs procedural control flow (validate the invitation, RAISE on
# a bad one, multiple dependent INSERTs) that a single SQL statement
# can't express. Row-locked (FOR UPDATE) so two concurrent accept
# attempts on the same token can't both succeed.
_ACCEPT_INVITATION_SQL = """
CREATE FUNCTION accept_invitation(
    p_token_hash text,
    p_password_hash text,
    p_mfa_secret_encrypted text
) RETURNS TABLE(user_id uuid, membership_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_invitation invitations%ROWTYPE;
    v_user_id uuid;
    v_membership_id uuid;
BEGIN
    SELECT * INTO v_invitation FROM invitations
        WHERE token_hash = p_token_hash
        FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'invitation not found';
    END IF;
    IF v_invitation.status <> 'pending' THEN
        RAISE EXCEPTION 'invitation is not pending';
    END IF;
    IF v_invitation.expires_at < now() THEN
        RAISE EXCEPTION 'invitation has expired';
    END IF;

    INSERT INTO users (subject, password_hash, mfa_secret_encrypted)
    VALUES (v_invitation.subject, p_password_hash, p_mfa_secret_encrypted)
    RETURNING id INTO v_user_id;

    INSERT INTO memberships (user_id, org_id, role, scope)
    VALUES (v_user_id, v_invitation.org_id, v_invitation.role, v_invitation.scope)
    RETURNING id INTO v_membership_id;

    INSERT INTO membership_facilities (membership_id, facility_id)
    SELECT v_membership_id, facility_id FROM invitation_facilities
    WHERE invitation_id = v_invitation.id;

    UPDATE invitations SET status = 'accepted', accepted_at = now()
    WHERE id = v_invitation.id;

    RETURN QUERY SELECT v_user_id, v_membership_id;
END;
$$;
"""


def _create_new_tables_if_missing() -> None:
    if context.is_offline_mode():
        for table_name in _NEW_TABLES:
            Base.metadata.tables[table_name].create(bind=op.get_bind(), checkfirst=False)
        op.add_column("memberships", sa.Column("revoked_at", sa.DateTime(timezone=True)))
        return

    inspector = inspect(op.get_bind())
    for table_name in _NEW_TABLES:
        if not inspector.has_table(table_name):
            Base.metadata.tables[table_name].create(bind=op.get_bind(), checkfirst=False)
    existing_membership_columns = {col["name"] for col in inspector.get_columns("memberships")}
    if "revoked_at" not in existing_membership_columns:
        op.add_column("memberships", sa.Column("revoked_at", sa.DateTime(timezone=True)))


def _grant_and_secure_new_tables() -> None:
    for table_name in _NEW_TABLES:
        op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table_name} TO {_APP_ROLE}"))
        op.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))

    op.execute(
        text(
            "CREATE POLICY org_access ON invitations "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    _accessible_invitation_ids = (
        "SELECT id FROM invitations WHERE org_id IN "
        "(SELECT org_id FROM resolve_accessible_org_ids(current_setting('app.user_id')::uuid))"
    )
    op.execute(
        text(
            "CREATE POLICY org_access ON invitation_facilities "
            f"USING (invitation_id IN ({_accessible_invitation_ids})) "
            f"WITH CHECK (invitation_id IN ({_accessible_invitation_ids}))"
        )
    )
    op.execute(
        text(
            "CREATE POLICY org_access ON api_keys "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    op.execute(
        text(
            "CREATE POLICY org_access ON org_policies "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _tighten_self_membership_policy_to_select_only() -> None:
    """Security fix, caught while adding the policy below: 0001's
    `self_membership` policy carried no `FOR SELECT` qualifier, which in
    Postgres means it applies to *every* command (SELECT, INSERT,
    UPDATE, DELETE) -- so `WITH CHECK (user_id = current_setting(...))`
    was already implicitly permitting any authenticated user to INSERT a
    membership row for *themselves* at any org_id/role/scope they chose
    (nothing checked which org or which role). No application code ever
    exercised this before this migration (Phase 4 built no
    membership-creation route at all), but this migration adds the
    first ones, so the latent gap gets closed here rather than carried
    forward. Self-access to `memberships` is now read-only; all writes
    go through `org_authoring`/`org_authoring_update` below (an
    already-authenticated caller with *resolved org access*, never bare
    self-identity) or `accept_invitation` (anonymous, token-authorized)."""
    op.execute(text("DROP POLICY self_membership ON memberships"))
    op.execute(
        text(
            "CREATE POLICY self_membership ON memberships "
            "FOR SELECT "
            "USING (user_id = current_setting('app.user_id')::uuid)"
        )
    )


def _add_membership_org_authoring_policy() -> None:
    """Additive to the (now SELECT-only) self-only policy above --
    Postgres OR's multiple permissive policies on the same table for the
    same command, so this doesn't replace it, just adds INSERT/UPDATE
    coverage. Lets an already-authenticated caller with resolved access
    to a membership's org create or revoke *someone else's* membership.
    Covers delegated admin, offboarding, and API-key provisioning; does
    NOT cover invitation acceptance (no authenticated caller exists then
    -- that's what accept_invitation is for)."""
    op.execute(
        text(
            "CREATE POLICY org_authoring ON memberships "
            "FOR INSERT "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    op.execute(
        text(
            "CREATE POLICY org_authoring_update ON memberships "
            "FOR UPDATE "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _tighten_and_extend_membership_facility_policy() -> None:
    """Same latent gap, same fix, on `membership_facilities`: 0001's
    `self_membership_facility` policy also carried no `FOR SELECT`
    qualifier, so `WITH CHECK (membership_id IN (my own memberships))`
    was already implicitly permitting a `SPECIFIC_FACILITIES`-scoped user
    to INSERT a row pairing their *own* membership with *any* facility_id
    in the system -- self-granting access to facilities their org never
    owned. Tightened to SELECT-only, plus a new org-authoring INSERT
    policy requiring the *caller's own* resolved facility access to
    include the facility being granted -- you can only hand out access
    you already have."""
    op.execute(text("DROP POLICY self_membership_facility ON membership_facilities"))
    op.execute(
        text(
            "CREATE POLICY self_membership_facility ON membership_facilities "
            "FOR SELECT "
            "USING (membership_id IN (SELECT id FROM memberships "
            "WHERE user_id = current_setting('app.user_id')::uuid))"
        )
    )
    op.execute(
        text(
            "CREATE POLICY org_authoring ON membership_facilities "
            "FOR INSERT "
            "WITH CHECK (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _replace_resolution_functions() -> None:
    facility_sql = _RESOLVE_FACILITY_IDS_SQL.format(
        org_tree_cte=_ORG_TREE_CTE.format(scope_filter=" AND m.scope = 'ALL_FACILITIES'")
    )
    org_sql = _RESOLVE_ORG_IDS_SQL.format(org_tree_cte=_ORG_TREE_CTE.format(scope_filter=""))
    op.execute(text(facility_sql))
    op.execute(text(org_sql))


def _create_invitation_functions() -> None:
    op.execute(text(_LOOKUP_INVITATION_BY_HASH_SQL))
    op.execute(text(_ACCEPT_INVITATION_SQL))
    function_signatures = (
        "get_invitation_by_token_hash(text)",
        "accept_invitation(text, text, text)",
    )
    for function_signature in function_signatures:
        op.execute(text(f"REVOKE ALL ON FUNCTION {function_signature} FROM PUBLIC"))
        op.execute(text(f"GRANT EXECUTE ON FUNCTION {function_signature} TO {_APP_ROLE}"))


def upgrade() -> None:
    _create_new_tables_if_missing()
    _grant_and_secure_new_tables()
    _tighten_self_membership_policy_to_select_only()
    _add_membership_org_authoring_policy()
    _tighten_and_extend_membership_facility_policy()
    _replace_resolution_functions()
    _create_invitation_functions()


def downgrade() -> None:
    op.execute(text("DROP FUNCTION IF EXISTS accept_invitation(text, text, text)"))
    op.execute(text("DROP FUNCTION IF EXISTS get_invitation_by_token_hash(text)"))
    op.execute(text("DROP POLICY IF EXISTS org_authoring ON membership_facilities"))
    op.execute(text("DROP POLICY IF EXISTS self_membership_facility ON membership_facilities"))
    op.execute(
        text(
            "CREATE POLICY self_membership_facility ON membership_facilities "
            "USING (membership_id IN (SELECT id FROM memberships "
            "WHERE user_id = current_setting('app.user_id')::uuid)) "
            "WITH CHECK (membership_id IN (SELECT id FROM memberships "
            "WHERE user_id = current_setting('app.user_id')::uuid))"
        )
    )
    op.execute(text("DROP POLICY IF EXISTS org_authoring_update ON memberships"))
    op.execute(text("DROP POLICY IF EXISTS org_authoring ON memberships"))
    op.execute(text("DROP POLICY IF EXISTS self_membership ON memberships"))
    op.execute(
        text(
            "CREATE POLICY self_membership ON memberships "
            "USING (user_id = current_setting('app.user_id')::uuid) "
            "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
        )
    )
    for table_name in reversed(_NEW_TABLES):
        op.execute(text(f"DROP POLICY IF EXISTS org_access ON {table_name}"))
    op.drop_column("memberships", "revoked_at")
    for table_name in reversed(_NEW_TABLES):
        Base.metadata.tables[table_name].drop(bind=op.get_bind())
