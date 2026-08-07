"""F-19 (docs/audit/REGISTER.md), extended for Phase 4's access model:
every table except the deliberately-ungated bootstrap set must have RLS
enabled, forced, and a policy -- proven here by walking `Base.metadata`
directly rather than trusting a hand-maintained list.

Before Phase 4, this walked columns named `tenant_id` -- a flat
per-table scoping column every RLS-protected table carried. That
assumption no longer holds: `organizations`/`facilities` are RLS-
protected via their own `id` (not a `facility_id`/`org_id` column
pointing at themselves), and `memberships`/`membership_facilities` are
protected by a bootstrap-safe self-only policy, not the recursive
resolution functions the business tables use. What's actually invariant
across all of them is simpler and doesn't need per-table-shape
knowledge: every table in `Base.metadata` gets RLS unless it's on the
short, explicitly-justified exclusion list below.

`alembic/versions/0001_initial_schema.py`'s own table lists
(`_FACILITY_SCOPED_TABLES`/`_ORG_SCOPED_TABLES`) are exactly the kind of
hand-maintained list that already drifted once for real pre-Phase-4:
`recovery_packets` needed its own migration (0004) to get RLS at all,
since 0001 predates that table's existence and nothing re-checked the
list against `db/models.py` afterward. This test is the backstop --
walking `Base.metadata` means a future table with no RLS migration fails
here instead of silently shipping unprotected.

`tests/db/test_rls_tenant_isolation.py` is a different, complementary
proof: it shows RLS actually *blocks* cross-facility/cross-org access
and correctly resolves the org hierarchy. This file proves *coverage* --
every table that should have RLS does -- not that RLS, where present,
works correctly.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from db import models  # noqa: F401 -- registers every table on Base.metadata
from db.base import Base

# Tables deliberately NOT RLS-protected. Currently just `users` -- see
# db.models.User's own docstring for why: resolving a bearer token's
# subject to a user id is how a session gets bootstrapped in the first
# place (src/api/auth.py), so this lookup can't itself require
# app.user_id to already be set. Any future addition here needs the same
# level of justification, not a convenience opt-out.
_DELIBERATELY_UNGATED_TABLES = frozenset({"users"})

_RLS_STATUS_QUERY = text(
    "SELECT relname, relrowsecurity, relforcerowsecurity "
    "FROM pg_class WHERE relname = ANY(:names)"
)
_POLICY_QUERY = text("SELECT DISTINCT tablename FROM pg_policies WHERE tablename = ANY(:names)")


def _resolved_access_table_names() -> list[str]:
    return sorted(
        table_name
        for table_name in Base.metadata.tables
        if table_name not in _DELIBERATELY_UNGATED_TABLES
    )


def test_resolved_access_table_list_is_not_accidentally_empty() -> None:
    """Guards the data-driven test below against a silently-broken
    fixture (e.g. db.models failing to import) making it vacuously
    pass -- fully offline, no database needed."""
    names = _resolved_access_table_names()
    assert len(names) >= 10
    assert "claims" in names
    assert "findings" in names
    assert "recovery_packets" in names
    assert "organizations" in names
    assert "facilities" in names
    assert "memberships" in names
    assert "membership_facilities" in names
    assert "contracts" in names
    assert "users" not in names


def test_deliberately_ungated_tables_list_is_exactly_the_justified_one() -> None:
    """Guards the exclusion list itself, fully offline -- if `users` ever
    gains RLS for real, or another table is added to this list, that
    must be a deliberate edit here too, not a silent drift."""
    assert _DELIBERATELY_UNGATED_TABLES == frozenset({"users"})


def test_every_resolved_access_table_has_rls_enabled_forced_and_a_policy(
    app_session_factory: sessionmaker[Session],
) -> None:
    table_names = _resolved_access_table_names()

    with app_session_factory() as session:
        rls_rows = session.execute(_RLS_STATUS_QUERY, {"names": table_names}).all()
        rls_status = {
            row.relname: (row.relrowsecurity, row.relforcerowsecurity) for row in rls_rows
        }

        tables_with_a_policy = set(
            session.execute(_POLICY_QUERY, {"names": table_names}).scalars().all()
        )

    missing_rls = [name for name in table_names if not rls_status.get(name, (False, False))[0]]
    missing_force = [name for name in table_names if not rls_status.get(name, (False, False))[1]]
    missing_policy = [name for name in table_names if name not in tables_with_a_policy]

    assert missing_rls == [], f"tables with RLS not enabled: {missing_rls}"
    assert missing_force == [], f"tables with RLS not forced: {missing_force}"
    assert missing_policy == [], f"tables with no RLS policy at all: {missing_policy}"
