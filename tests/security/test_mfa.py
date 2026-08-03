"""Tests for TOTP enrollment and verification."""

from __future__ import annotations

import pyotp

from security.mfa import generate_enrollment_secret, provisioning_uri, verify_code


def test_generate_enrollment_secret_is_a_valid_base32_totp_secret() -> None:
    secret = generate_enrollment_secret()
    # pyotp.TOTP() raises on a malformed base32 secret -- constructing one
    # successfully and generating a code is itself the validity check.
    code = pyotp.TOTP(secret).now()
    assert len(code) == 6
    assert code.isdigit()


def test_two_enrollment_secrets_are_different() -> None:
    assert generate_enrollment_secret() != generate_enrollment_secret()


def test_verify_code_accepts_the_current_code() -> None:
    secret = generate_enrollment_secret()
    current_code = pyotp.TOTP(secret).now()
    assert verify_code(secret, current_code) is True


def test_verify_code_rejects_a_wrong_code() -> None:
    secret = generate_enrollment_secret()
    real_code = pyotp.TOTP(secret).now()
    wrong_code = "000000" if real_code != "000000" else "111111"
    assert verify_code(secret, wrong_code) is False


def test_verify_code_rejects_a_code_from_a_different_secret() -> None:
    secret_a = generate_enrollment_secret()
    secret_b = generate_enrollment_secret()
    code_from_b = pyotp.TOTP(secret_b).now()
    # Extremely unlikely but not impossible for two independent TOTP
    # secrets to momentarily produce the same 6-digit code by chance.
    code_from_a = pyotp.TOTP(secret_a).now()
    if code_from_a == code_from_b:
        return
    assert verify_code(secret_a, code_from_b) is False


def test_provisioning_uri_is_a_scannable_otpauth_uri() -> None:
    secret = generate_enrollment_secret()
    uri = provisioning_uri(secret, "biller@example.com", issuer="ASC Recovery Test")
    assert uri.startswith("otpauth://totp/")
    assert "ASC%20Recovery%20Test" in uri or "ASC+Recovery+Test" in uri
    assert "biller%40example.com" in uri
