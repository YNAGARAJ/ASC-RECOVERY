"""TOTP-based multi-factor authentication (RFC 6238), via `pyotp`.

MFA is mandatory for every user and every session, no exceptions and no
internal-user bypass -- this module only provides enrollment and
verification; the actual enforcement ("no session without a verified MFA
code") lives in session.py, which is the single choke point for minting a
session.
"""

from __future__ import annotations

import pyotp

_DEFAULT_ISSUER = "ASC Recovery"


def generate_enrollment_secret() -> str:
    """A new base32 TOTP secret for a user enrolling in MFA. This is
    effectively a long-lived credential and must be stored encrypted
    (security.encryption.EnvelopeEncryptor), never in plaintext."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str, issuer: str = _DEFAULT_ISSUER) -> str:
    """An `otpauth://` URI for a user to scan into an authenticator app
    during enrollment."""
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_code(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Checks a 6-digit TOTP code against the secret, allowing a small
    window (default: one 30s step either side) for clock drift."""
    return pyotp.TOTP(secret).verify(code, valid_window=valid_window)
