"""Per-transaction access scoping for Row-Level Security (Phase 4,
`docs/MASTER-BUILD-PROMPT-V2.md`).

Every read and write of a resolved-access table must happen inside a
transaction where `app.user_id` has been set -- RLS policies
(`alembic/versions/0001_initial_schema.py`) read this session-local
setting and resolve accessible facilities/orgs from it via
`resolve_accessible_facility_ids`/`resolve_accessible_org_ids`;
application-level filtering is defense in depth, never the actual
boundary.

Unlike the retired `tenant_session` this replaces, there is no
`org_id`/`facility_id` to set alongside it: the resolution functions take
only `p_user_id` and return *every* facility/org that user can reach
across *all* their memberships -- RLS is the security ceiling. Which
specific org a request is currently "acting as" (for role resolution and
write-path facility/org targeting) is an application-layer concern
carried on `AuthContext`/the JWT's `active_org_id` claim
(`security/session.py`, `api/auth.py`), not a second Postgres session
variable -- narrower than RLS on purpose, since a UI/API scope selection
isn't a security boundary the same way row visibility is.

Uses `set_config('app.user_id', ..., true)` rather than
`SET LOCAL app.user_id = ...`: the third `true` argument is Postgres's
"is_local" flag, equivalent to `SET LOCAL` (transaction-scoped, never
leaking into the next request on a pooled connection) -- but `set_config`
is a normal function call, so the user id can be passed as a real bind
parameter instead of being string-formatted into the SQL text, which `SET`
cannot do safely (the SET command's grammar does not accept a placeholder
for its value in the general case).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


@contextmanager
def access_session(
    session_factory: sessionmaker[Session], user_id: uuid.UUID
) -> Iterator[Session]:
    """Opens a transaction scoped to a single user's resolved access.
    Commits on clean exit, rolls back on exception. There is nothing to
    reset afterward: the transaction-local setting simply ceases to exist
    once it ends."""
    with session_factory() as session, session.begin():
        session.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(user_id)},
        )
        yield session
