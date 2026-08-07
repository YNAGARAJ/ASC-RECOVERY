"""Tests for the Text-column serialization glue between EnvelopeEncryptor
and the `claims.patient_name_encrypted`/`patient_member_id_encrypted`
columns. Pure -- no database needed; tests/db/test_patient_columns_are_encrypted.py
covers the actual column round-trip against real Postgres."""

from __future__ import annotations

import pytest

from security.encryption import DecryptionError, EnvelopeEncryptor
from security.kms_local import LocalKMS
from security.phi_columns import decrypt_phi_field, encrypt_phi_field


def _encryptor() -> EnvelopeEncryptor:
    kms = LocalKMS()
    kms.generate_kek("test-kek")
    return EnvelopeEncryptor(kms)


def test_encrypt_then_decrypt_round_trips() -> None:
    encryptor = _encryptor()
    serialized = encrypt_phi_field(encryptor, "PATIENT ONE")
    assert decrypt_phi_field(encryptor, serialized) == "PATIENT ONE"


def test_encrypting_none_returns_none() -> None:
    encryptor = _encryptor()
    assert encrypt_phi_field(encryptor, None) is None


def test_decrypting_none_returns_none() -> None:
    encryptor = _encryptor()
    assert decrypt_phi_field(encryptor, None) is None


def test_serialized_form_does_not_contain_the_plaintext() -> None:
    encryptor = _encryptor()
    serialized = encrypt_phi_field(encryptor, "A VERY IDENTIFIABLE PATIENT NAME")
    assert serialized is not None
    assert "A VERY IDENTIFIABLE PATIENT NAME" not in serialized


def test_decrypting_with_a_different_kms_instance_fails() -> None:
    encryptor_a = _encryptor()
    serialized = encrypt_phi_field(encryptor_a, "PATIENT ONE")

    kms_b = LocalKMS()
    kms_b.generate_kek("test-kek")
    encryptor_b = EnvelopeEncryptor(kms_b)

    with pytest.raises(DecryptionError):
        decrypt_phi_field(encryptor_b, serialized)


def test_round_trips_unicode_names() -> None:
    encryptor = _encryptor()
    serialized = encrypt_phi_field(encryptor, "PATIENT NAME WITH ACCENTS: JOSE MUNOZ")
    assert decrypt_phi_field(encryptor, serialized) == "PATIENT NAME WITH ACCENTS: JOSE MUNOZ"


def test_encrypt_with_an_explicit_kek_id_stores_it_and_still_round_trips() -> None:
    """Phase 6, per-org encryption keys: `kek_id` is forwarded straight
    through to `EnvelopeEncryptor.encrypt`, and the serialized form
    carries whichever kek_id was actually used, same as always."""
    kms = LocalKMS()
    kms.generate_kek("test-kek")
    kms.generate_kek("org-dedicated-key")
    encryptor = EnvelopeEncryptor(kms)

    serialized = encrypt_phi_field(encryptor, "PATIENT ONE", kek_id="org-dedicated-key")

    assert serialized is not None
    assert '"kek_id": "org-dedicated-key"' in serialized
    assert decrypt_phi_field(encryptor, serialized) == "PATIENT ONE"
