"""Per-org IP allowlist matching (Phase 5 step 6).

Pure function, no FastAPI/`Request` dependency -- `api/auth.py` calls this
from `_resolve_auth_context` after extracting the request's client IP.
Kept separate and pure so the matching logic (CIDR support, malformed
entries, an unparseable client IP) is fully unit-testable without an HTTP
request in the loop at all.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence


def ip_allowed(client_ip: str | None, allowlist: Sequence[str]) -> bool:
    """`allowlist` entries may be a bare IP or a CIDR range (both parse via
    `ip_network`); a malformed entry is skipped rather than raising -- one
    bad entry in an admin-edited list should not disable the whole policy.
    An unparseable or missing `client_ip` never matches anything -- fail
    closed, since failing open would let a broken or unexpected client
    address silently bypass the restriction it's supposed to enforce."""
    if client_ip is None:
        return False
    try:
        parsed_ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if parsed_ip in network:
            return True
    return False
