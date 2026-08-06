"""Installs `security.redaction.PHIRedactionFilter` structurally, on a
handler attached to the ROOT logger (F-10, docs/audit/REGISTER.md) --
not on any one named logger.

Python's logging module only ever consults a `Logger`'s own filters for
calls made directly on that logger; propagation to ancestor loggers walks
their HANDLERS, not their filters. That distinction is exactly why
`api.errors`'s existing `logger.addFilter(PHIRedactionFilter())` only
ever protects the `api.errors` logger specifically -- any other logger
anywhere in this codebase, present or future (`api.request` already is
one), silently bypasses it entirely. A handler attached to the root
logger receives every propagated record from every logger in the
process by construction, with no per-module opt-in required. `api.errors`
keeps its own direct attachment too -- redundant once this is wired in,
kept anyway as a fail-safe that doesn't depend on `configure_logging`
having been called first.
"""

from __future__ import annotations

import logging
from typing import TextIO

from security.redaction import PHIRedactionFilter


def configure_logging(
    *, level: int = logging.INFO, stream: TextIO | None = None
) -> logging.Handler:
    """Call once at process startup (`main.py`). Calling it again (e.g.
    across multiple test-only `create_app_from_env()` calls) is harmless
    -- it just adds another independent handler, the same way
    `observability.tracing.setup_tracing`/`observability.metrics.setup_metrics`
    already behave on repeated calls -- but is not the intended
    production use, and real callers should only ever do it once.
    """
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(PHIRedactionFilter())
    root.addHandler(handler)
    return handler
