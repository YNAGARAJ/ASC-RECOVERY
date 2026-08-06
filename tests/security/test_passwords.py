"""Tests for password hashing -- round trip, wrong password, and every
malformed/foreign `encoded` shape a caller might pass to `verify_password`
must return False rather than raise."""

from __future__ import annotations

from security.passwords import hash_password, verify_password


def test_verify_accepts_the_correct_password() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded) is True


def test_verify_rejects_a_wrong_password() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("wrong password", encoded) is False


def test_two_hashes_of_the_same_password_differ() -> None:
    """Random per-call salt -- proves hash_password isn't just a bare digest."""
    assert hash_password("same password") != hash_password("same password")


def test_verify_rejects_malformed_encoded_value() -> None:
    assert verify_password("anything", "not-a-valid-encoded-hash") is False


def test_verify_rejects_wrong_field_count() -> None:
    assert verify_password("anything", "scrypt$16384$8$1$deadbeef") is False


def test_verify_rejects_a_foreign_scheme() -> None:
    assert verify_password("anything", "bcrypt$16384$8$1$aa$bb") is False


def test_verify_rejects_non_hex_salt_or_hash() -> None:
    assert verify_password("anything", "scrypt$16384$8$1$not-hex$also-not-hex") is False


def test_verify_rejects_non_integer_cost_parameters() -> None:
    assert verify_password("anything", "scrypt$oops$8$1$aa$bb") is False
