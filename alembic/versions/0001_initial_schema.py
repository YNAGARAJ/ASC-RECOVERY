"""Initial schema: organizations, facilities, memberships, effective-dated
contracts, remittances, claims, findings, append-only audit log -- with
Row-Level Security against *resolved facility/org access* (Phase 4,
`docs/MASTER-BUILD-PROMPT-V2.md`) as the isolation enforcement mechanism.

This is a clean-cut replacement of the earlier flat `tenants`/`tenant_id`
model, not a data migration -- no real customer/PHI data has ever existed
in this system (CLAUDE.md rule 1; no pilot has run). `db/models.py` (via
`Base.metadata.create_all`, called below) is the single source of truth
for table shape, same convention as before; this file's own responsibility
is exactly the security-critical parts that need hand-authored control:
the recursive access-resolution functions, RLS policies, and grants.

Runs as the table-owning role (`asc_owner` in local dev, see
`docs/DB_SETUP.md`).

**Precondition: `asc_owner` must have the `BYPASSRLS` attribute.** The two
access-resolution functions below are `SECURITY DEFINER`, owned by
whoever runs this migration (`asc_owner`), specifically so they can walk
`organizations`/`facilities`/`memberships`/`membership_facilities`
internally without being blocked by those same tables' own new RLS
policies -- otherwise resolving "which orgs can this user reach" would
require already knowing which orgs this user can reach. A regular
(non-superuser) `CREATEDB`-only role cannot grant itself `BYPASSRLS`; this
is a one-time grant made by whoever provisions the database as a real
superuser, same tier of operation as creating `asc_owner` itself. Docker's
bootstrap `POSTGRES_USER` is already a full superuser by convention (see
`docker-compose.yml`), so this is a no-op there; a manually-provisioned
real Postgres install needs `ALTER ROLE asc_owner BYPASSRLS;` run once as
superuser (see `docs/DB_SETUP.md`). `asc_app` (the application runtime
role) never gets `BYPASSRLS` itself -- only `EXECUTE` on these two
specific, read-only functions.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from db import models  # noqa: F401 -- registers all tables on Base.metadata
from db.base import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Business/PHI tables scoped by facility -- one row's visibility resolves
# through resolve_accessible_facility_ids().
_FACILITY_SCOPED_TABLES = (
    "remittances",
    "claims",
    "service_lines",
    "adjustments",
    "findings",
    "audit_log",
    "phi_access_log",
)

# Payer contracts are negotiated per organization, not per facility -- one
# ASC_GROUP's facilities share one rate card. Resolves through
# resolve_accessible_org_ids().
_ORG_SCOPED_TABLES = (
    "contracts",
    "contract_versions",
    "fee_schedule_lines",
)

# Append-only: no UPDATE/DELETE grant for the application role, ever.
_APPEND_ONLY_TABLES = ("audit_log", "phi_access_log")

# Mutable operational tables: SELECT/INSERT/UPDATE, never DELETE -- retention
# is enforced by soft-delete (`deleted_at`) columns where applicable, per
# the 6-year HIPAA documentation requirement.
_MUTABLE_TABLES = (
    "organizations",
    "facilities",
    "memberships",
    "membership_facilities",
) + _ORG_SCOPED_TABLES + (
    "remittances",
    "claims",
    "service_lines",
    "adjustments",
    "findings",
)

_APP_ROLE = "asc_app"

# One recursive walk of the org hierarchy, shared by both resolution
# functions below -- SPECIFIC_FACILITIES-scoped memberships intentionally
# do NOT walk this tree (a facility named directly in
# `membership_facilities` is authorized regardless of hierarchy depth);
# only ALL_FACILITIES-scoped memberships need "this org or any descendant".
# The `visited` array is an explicit cycle guard -- a corrupted
# `parent_org_id` chain must terminate, not hang the connection.
_ORG_TREE_CTE = """
    WITH RECURSIVE org_tree AS (
        SELECT o.id, ARRAY[o.id] AS visited
        FROM memberships m
        JOIN organizations o ON o.id = m.org_id
        WHERE m.user_id = p_user_id{scope_filter}
        UNION ALL
        SELECT child.id, org_tree.visited || child.id
        FROM organizations child
        JOIN org_tree ON child.parent_org_id = org_tree.id
        WHERE NOT (child.id = ANY(org_tree.visited))
    )
"""

_RESOLVE_FACILITY_IDS_SQL = f"""
CREATE FUNCTION resolve_accessible_facility_ids(p_user_id uuid)
RETURNS TABLE(facility_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    {_ORG_TREE_CTE.format(scope_filter=" AND m.scope = 'ALL_FACILITIES'")}
    SELECT DISTINCT f.id
    FROM facilities f
    JOIN org_tree ot ON f.org_id = ot.id
    UNION
    SELECT mf.facility_id
    FROM memberships m
    JOIN membership_facilities mf ON mf.membership_id = m.id
    WHERE m.user_id = p_user_id AND m.scope = 'SPECIFIC_FACILITIES';
$$;
"""

_RESOLVE_ORG_IDS_SQL = f"""
CREATE FUNCTION resolve_accessible_org_ids(p_user_id uuid)
RETURNS TABLE(org_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    {_ORG_TREE_CTE.format(scope_filter="")}
    SELECT DISTINCT id FROM org_tree;
$$;
"""


def _create_access_resolution_functions() -> None:
    op.execute(text(_RESOLVE_FACILITY_IDS_SQL))
    op.execute(text(_RESOLVE_ORG_IDS_SQL))
    function_signatures = (
        "resolve_accessible_facility_ids(uuid)",
        "resolve_accessible_org_ids(uuid)",
    )
    for function_signature in function_signatures:
        op.execute(text(f"REVOKE ALL ON FUNCTION {function_signature} FROM PUBLIC"))
        op.execute(text(f"GRANT EXECUTE ON FUNCTION {function_signature} TO {_APP_ROLE}"))


def _secure_facility_scoped(table: str) -> None:
    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            f"CREATE POLICY facility_access ON {table} "
            "USING (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (facility_id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _secure_org_scoped(table: str) -> None:
    op.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            f"CREATE POLICY org_access ON {table} "
            "USING (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (org_id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )


def _secure_identity_tables() -> None:
    # `organizations`/`facilities`: visible if resolved-accessible, same
    # mechanism as the business tables above.
    op.execute(text("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE organizations FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY org_access ON organizations "
            "USING (id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (id IN (SELECT org_id FROM "
            "resolve_accessible_org_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    op.execute(text("ALTER TABLE facilities ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE facilities FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY facility_access ON facilities "
            "USING (id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid))) "
            "WITH CHECK (id IN (SELECT facility_id FROM "
            "resolve_accessible_facility_ids(current_setting('app.user_id')::uuid)))"
        )
    )
    # `memberships`: bootstrap-safe *self-only* equality, deliberately not
    # routed through the recursive functions above -- see db/models.py's
    # Membership docstring for why (broader org_admin visibility into
    # other users' memberships is a Phase 5 concern, added later as an
    # *additional* OR'd policy, not a replacement for this one).
    op.execute(text("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE memberships FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY self_membership ON memberships "
            "USING (user_id = current_setting('app.user_id')::uuid) "
            "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
        )
    )
    op.execute(text("ALTER TABLE membership_facilities ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE membership_facilities FORCE ROW LEVEL SECURITY"))
    op.execute(
        text(
            "CREATE POLICY self_membership_facility ON membership_facilities "
            "USING (membership_id IN (SELECT id FROM memberships "
            "WHERE user_id = current_setting('app.user_id')::uuid)) "
            "WITH CHECK (membership_id IN (SELECT id FROM memberships "
            "WHERE user_id = current_setting('app.user_id')::uuid))"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=False)

    for table in _MUTABLE_TABLES:
        op.execute(text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {_APP_ROLE}"))

    for table in _APPEND_ONLY_TABLES:
        op.execute(text(f"GRANT SELECT, INSERT ON {table} TO {_APP_ROLE}"))
        op.execute(text(f"REVOKE UPDATE, DELETE ON {table} FROM {_APP_ROLE}"))

    _create_access_resolution_functions()
    _secure_identity_tables()

    for table in _FACILITY_SCOPED_TABLES:
        _secure_facility_scoped(table)
    for table in _ORG_SCOPED_TABLES:
        _secure_org_scoped(table)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_FACILITY_SCOPED_TABLES):
        op.execute(text(f"DROP POLICY IF EXISTS facility_access ON {table}"))
    for table in reversed(_ORG_SCOPED_TABLES):
        op.execute(text(f"DROP POLICY IF EXISTS org_access ON {table}"))
    op.execute(text("DROP POLICY IF EXISTS self_membership_facility ON membership_facilities"))
    op.execute(text("DROP POLICY IF EXISTS self_membership ON memberships"))
    op.execute(text("DROP POLICY IF EXISTS facility_access ON facilities"))
    op.execute(text("DROP POLICY IF EXISTS org_access ON organizations"))
    op.execute(text("DROP FUNCTION IF EXISTS resolve_accessible_org_ids(uuid)"))
    op.execute(text("DROP FUNCTION IF EXISTS resolve_accessible_facility_ids(uuid)"))
    Base.metadata.drop_all(bind=bind)
