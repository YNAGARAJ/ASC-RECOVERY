"""Password hashing for `users.password_hash` (F-04/F-05,
docs/audit/REGISTER.md). Uses `hashlib.scrypt` -- part of the standard
library (linked against OpenSSL), not a new dependency -- rather than
adding bcrypt/argon2 to pyproject.toml for a single call site.

Parameters (N=2**14, r=8, p=1) match Django's own scrypt defaults, a
reasonable balance of cost and login latency without a live benchmark
environment to tune against.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_SCHEME = "scrypt"
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_DKLEN = 64


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """False for a wrong password AND for a malformed/foreign-scheme
    `encoded` value -- a caller must never treat an exception here as
    proof of anything about `password`."""
    parts = encoded.split("$")
    if len(parts) != 6:
        return False
    scheme, n, r, p, salt_hex, hash_hex = parts
    if scheme != _SCHEME:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, OverflowError):
        return False
    return hmac.compare_digest(derived, expected)
