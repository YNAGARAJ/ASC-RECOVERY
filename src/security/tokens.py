"""Bearer tokens shown to a caller exactly once (invitation-acceptance
links, API keys) -- never stored raw, mirroring `security/passwords.py`'s
never-store-raw discipline for actual passwords. Unlike a password, a
token is looked up by exact-match equality (`token_hash = ...`), not
verified against a user-typed guess, so this uses a fast, deterministic
hash (SHA-256) rather than a slow, salted one (scrypt) -- the security
property here is "unguessable 256-bit random value," not "resistant to
offline brute force of a low-entropy secret," so scrypt's slowness would
only make every lookup expensive for no benefit.
"""

from __future__ import annotations

import hashlib
import secrets

# Phase 5 step 5: what lets api/auth.py branch on "is this bearer token an
# API key or a JWT" *before* attempting either kind of validation, without
# a DB round-trip just to find out. Safe by construction against colliding
# with a real JWT: every JWT starts with the base64 of '{"' (`eyJ`),
# never with `ask_`.
API_KEY_PREFIX = "ask_"


def generate_token() -> str:
    """A URL-safe random token with 256 bits of entropy -- shown to the
    caller exactly once, at creation time."""
    return secrets.token_urlsafe(32)


def generate_api_key() -> str:
    """Same 256-bit-random-token discipline as `generate_token`, prefixed
    with `API_KEY_PREFIX` so it's self-identifying on presentation. The
    prefix is part of the hashed/stored value -- `hash_token` sees the
    whole string, never stripped, since the hash only ever needs to be
    compared against itself, never decomposed."""
    return f"{API_KEY_PREFIX}{generate_token()}"


def hash_token(raw_token: str) -> str:
    """Hex-encoded SHA-256 -- what's actually stored (`token_hash` on
    `Invitation`/`ApiKey`) and what a lookup by a presented token hashes
    and compares against."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
