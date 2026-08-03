"""Per-transaction tenant scoping for Row-Level Security.

Every read and write of a PHI-bearing table must happen inside a
transaction where `app.tenant_id` has been set -- RLS policies
(alembic/versions/0001_initial_schema.py) read this session-local setting;
application-level `WHERE tenant_id = ...` filtering is defense in depth,
never the actual boundary.

Uses `set_config('app.tenant_id', ..., true)` rather than
`SET LOCAL app.tenant_id = ...`: the third `true` argument is Postgres's
"is_local" flag, equivalent to `SET LOCAL` (transaction-scoped, never
leaking into the next request on a pooled connection) -- but `set_config`
is a normal function call, so the tenant id can be passed as a real bind
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
def tenant_session(
    session_factory: sessionmaker[Session], tenant_id: uuid.UUID
) -> Iterator[Session]:
    """Opens a transaction scoped to a single tenant. Commits on clean exit,
    rolls back on exception. There is nothing to reset afterward: the
    transaction-local setting simply ceases to exist once it ends."""
    with session_factory() as session, session.begin():
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        yield session
