"""Tests for envelope encryption: round-trip, tamper detection, wrong-key
failure, and the property that makes key rotation cheap -- rotating the
KEK touches only the wrapped DEK, never the bulk ciphertext.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from security.encryption import DecryptionError, EnvelopeEncryptor
from security.kms_local import LocalKMS


def _kms_with_kek(kek_id: str = "kek-v1") -> LocalKMS:
    kms = LocalKMS()
    kms.generate_kek(kek_id)
    return kms


def test_encrypt_then_decrypt_round_trips() -> None:
    encryptor = EnvelopeEncryptor(_kms_with_kek())
    plaintext = b"PATIENT ONE / TESTMBR000001"

    payload = encryptor.encrypt(plaintext)
    assert encryptor.decrypt(payload) == plaintext


def test_ciphertext_is_not_the_plaintext() -> None:
    encryptor = EnvelopeEncryptor(_kms_with_kek())
    plaintext = b"a very identifiable patient name"

    payload = encryptor.encrypt(plaintext)
    assert plaintext not in payload.ciphertext
    assert plaintext not in payload.wrapped_dek


def test_payload_records_the_kek_id_used() -> None:
    encryptor = EnvelopeEncryptor(_kms_with_kek("kek-v7"))
    payload = encryptor.encrypt(b"data")
    assert payload.kek_id == "kek-v7"


def test_tampered_ciphertext_fails_to_decrypt() -> None:
    encryptor = EnvelopeEncryptor(_kms_with_kek())
    payload = encryptor.encrypt(b"tamper me")

    tampered_bytes = bytearray(payload.ciphertext)
    tampered_bytes[0] ^= 0xFF
    tampered = replace(payload, ciphertext=bytes(tampered_bytes))

    with pytest.raises(DecryptionError):
        encryptor.decrypt(tampered)


def test_decrypting_with_the_wrong_kms_instance_fails() -> None:
    kms_a = _kms_with_kek("kek-v1")
    encryptor_a = EnvelopeEncryptor(kms_a)
    payload = encryptor_a.encrypt(b"secret")

    kms_b = _kms_with_kek("kek-v1")  # a different KMS, same kek_id, different bytes
    encryptor_b = EnvelopeEncryptor(kms_b)

    with pytest.raises(DecryptionError):
        encryptor_b.decrypt(payload)


def test_decrypting_with_an_unknown_kek_id_fails() -> None:
    encryptor = EnvelopeEncryptor(_kms_with_kek())
    payload = encryptor.encrypt(b"secret")
    orphaned = replace(payload, kek_id="no-such-kek")

    with pytest.raises(DecryptionError):
        encryptor.decrypt(orphaned)


def test_rotate_kek_only_changes_the_wrapped_dek_not_the_ciphertext() -> None:
    kms = _kms_with_kek("kek-v1")
    encryptor = EnvelopeEncryptor(kms)
    original = encryptor.encrypt(b"rotate me but don't re-encrypt me")  # under kek-v1

    kms.generate_kek("kek-v2")
    rotated = encryptor.rotate_kek(original, "kek-v2")

    assert rotated.ciphertext == original.ciphertext
    assert rotated.nonce == original.nonce
    assert original.kek_id == "kek-v1"
    assert rotated.kek_id == "kek-v2"
    assert rotated.wrapped_dek != original.wrapped_dek


def test_rotate_kek_produces_a_payload_that_still_decrypts_correctly() -> None:
    kms = _kms_with_kek("kek-v1")
    encryptor = EnvelopeEncryptor(kms)
    plaintext = b"the actual PHI value"

    original = encryptor.encrypt(plaintext)  # under kek-v1
    kms.generate_kek("kek-v2")
    rotated = encryptor.rotate_kek(original, "kek-v2")

    assert encryptor.decrypt(rotated) == plaintext


def test_rotate_kek_does_not_mutate_or_break_the_original_payload() -> None:
    """rotate_kek returns a new payload; the original is untouched and, as
    long as its KEK hasn't been deleted from the store, still decrypts."""
    kms = _kms_with_kek("kek-v1")
    encryptor = EnvelopeEncryptor(kms)
    plaintext = b"still readable via the old kek"

    original = encryptor.encrypt(plaintext)  # encrypted under kek-v1
    kms.generate_kek("kek-v2")  # simulate rotating to a new current KEK
    encryptor.rotate_kek(original, "kek-v2")

    assert encryptor.decrypt(original) == plaintext
    assert original.kek_id == "kek-v1"
