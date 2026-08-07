"""Pure matching logic for Phase 5 step 6's per-org IP allowlist -- no
FastAPI/`Request` dependency, so every branch (CIDR ranges, malformed
entries, an unparseable or missing client IP) is testable without an HTTP
request in the loop. `tests/api/test_org_policy.py` proves the wiring
into `api/auth.py` is real; this proves the matching itself is correct.
"""

from __future__ import annotations

from security.ip_allowlist import ip_allowed


def test_exact_ip_match() -> None:
    assert ip_allowed("203.0.113.5", ["203.0.113.5"]) is True


def test_ip_outside_allowlist_is_rejected() -> None:
    assert ip_allowed("203.0.113.5", ["198.51.100.1"]) is False


def test_cidr_range_match() -> None:
    assert ip_allowed("10.0.4.17", ["10.0.0.0/16"]) is True


def test_ip_outside_cidr_range_is_rejected() -> None:
    assert ip_allowed("10.1.4.17", ["10.0.0.0/16"]) is False


def test_matches_any_entry_in_a_multi_entry_allowlist() -> None:
    assert ip_allowed("198.51.100.9", ["203.0.113.5", "198.51.100.0/24"]) is True


def test_empty_allowlist_matches_nothing() -> None:
    assert ip_allowed("203.0.113.5", []) is False


def test_missing_client_ip_is_rejected() -> None:
    assert ip_allowed(None, ["203.0.113.5"]) is False


def test_unparseable_client_ip_is_rejected() -> None:
    """`TestClient` (the harness `tests/api/` uses) always presents the
    literal string `"testclient"` as the request's client host -- never a
    real IP. This proves that fails closed rather than crashing or
    silently matching, which is also why an HTTP-level test can only ever
    prove the *rejection* path end to end in this environment (see
    `tests/api/test_org_policy.py`)."""
    assert ip_allowed("testclient", ["0.0.0.0/0"]) is False


def test_malformed_allowlist_entry_is_skipped_not_raised() -> None:
    """One bad entry in an admin-edited list must not crash the check or
    silently disable the whole policy -- the well-formed entry alongside
    it still works."""
    assert ip_allowed("203.0.113.5", ["not-an-ip", "203.0.113.5"]) is True


def test_ipv6_address_is_supported() -> None:
    assert ip_allowed("2001:db8::1", ["2001:db8::/32"]) is True
