"""Tests for the stopgap KeyManagementService adapter -- same wrap/unwrap
round-trip and failure-mode proofs as tests/security/test_encryption.py's
LocalKMS coverage, plus the secret-shape validation that's unique to
reading a KEK from a secret instead of generating one in-process."""

from __future__ import annotations

import base64

import pytest

from security.kms_env import EnvKMS
from security.secrets import EnvSecretStore

_VALID_KEY_B64 = base64.b64encode(b"0" * 32).decode()


def test_wrap_then_unwrap_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore())

    dek = b"a-32-byte-data-encryption-key!!!"
    wrapped = kms.wrap_key(kms.current_kek_id(), dek)

    assert kms.unwrap_key(kms.current_kek_id(), wrapped) == dek


def test_wrapped_dek_is_not_the_plaintext_dek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore())

    dek = b"another-32-byte-dek-value-here!"
    wrapped = kms.wrap_key(kms.current_kek_id(), dek)

    assert dek not in wrapped


def test_current_kek_id_is_stable_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore())

    assert kms.current_kek_id() == kms.current_kek_id()


def test_unwrap_with_an_unknown_kek_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore())
    wrapped = kms.wrap_key(kms.current_kek_id(), b"a-32-byte-data-encryption-key!!!")

    with pytest.raises(KeyError):
        kms.unwrap_key("no-such-kek", wrapped)


def test_wrap_with_an_unknown_kek_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore())

    with pytest.raises(KeyError):
        kms.wrap_key("no-such-kek", b"a-32-byte-data-encryption-key!!!")


def test_two_independently_constructed_instances_from_the_same_secret_interoperate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The KEK is derived from the secret, not generated in-process (unlike
    LocalKMS) -- this is what lets it survive a process restart: a second
    process reading the same secret must be able to unwrap what the first
    wrapped."""
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", _VALID_KEY_B64)
    kms_a = EnvKMS(EnvSecretStore())
    kms_b = EnvKMS(EnvSecretStore())

    dek = b"a-32-byte-data-encryption-key!!!"
    wrapped = kms_a.wrap_key(kms_a.current_kek_id(), dek)

    assert kms_b.unwrap_key(kms_b.current_kek_id(), wrapped) == dek


@pytest.mark.parametrize(
    "raw_key",
    [b"too-short", b"0" * 16, b"0" * 33, b""],
)
def test_rejects_a_secret_that_does_not_decode_to_32_bytes(
    monkeypatch: pytest.MonkeyPatch, raw_key: bytes
) -> None:
    monkeypatch.setenv("PHI_ENCRYPTION_KEY", base64.b64encode(raw_key).decode())

    with pytest.raises(ValueError, match="32 bytes"):
        EnvKMS(EnvSecretStore())


def test_uses_the_given_secret_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOM_KEY_NAME", _VALID_KEY_B64)
    kms = EnvKMS(EnvSecretStore(), secret_name="CUSTOM_KEY_NAME")

    dek = b"a-32-byte-data-encryption-key!!!"
    wrapped = kms.wrap_key(kms.current_kek_id(), dek)
    assert kms.unwrap_key(kms.current_kek_id(), wrapped) == dek
