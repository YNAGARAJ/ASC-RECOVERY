"""Tests for the AWS KMS adapter (F-20, docs/audit/REGISTER.md), against
a fake boto3-shaped KMS client -- no real AWS account needed, matching
this repo's usual fake-client pattern for adapters that can't be
verified against real infrastructure here (see
tests/ingestion/test_sources.py's fake SFTP/S3 clients).
"""

from __future__ import annotations

import pytest

from security.kms_aws import AwsKmsAdapter

_KEY_ID = "alias/asc-recovery-kek"


class _FakeKmsClient:
    """Wraps by XOR-ing with a fixed pad -- not real cryptography, just
    enough to prove wrap/unwrap round-trips and that a mismatched
    ciphertext doesn't come back as the original plaintext."""

    _PAD = b"\x42" * 64

    def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, bytes]:  # noqa: N803
        pad = self._PAD[: len(Plaintext)]
        ciphertext = bytes(a ^ b for a, b in zip(Plaintext, pad, strict=True))
        return {"CiphertextBlob": ciphertext}

    def decrypt(self, *, CiphertextBlob: bytes, KeyId: str) -> dict[str, bytes]:  # noqa: N803
        pad = self._PAD[: len(CiphertextBlob)]
        plaintext = bytes(a ^ b for a, b in zip(CiphertextBlob, pad, strict=True))
        return {"Plaintext": plaintext}


def test_wrap_then_unwrap_round_trips() -> None:
    kms = AwsKmsAdapter(_FakeKmsClient(), _KEY_ID)
    dek = b"a-32-byte-data-encryption-key!!!"

    wrapped = kms.wrap_key(kms.current_kek_id(), dek)

    assert kms.unwrap_key(kms.current_kek_id(), wrapped) == dek


def test_current_kek_id_is_the_configured_key_id() -> None:
    kms = AwsKmsAdapter(_FakeKmsClient(), _KEY_ID)
    assert kms.current_kek_id() == _KEY_ID


def test_wrap_with_a_different_kek_id_raises() -> None:
    kms = AwsKmsAdapter(_FakeKmsClient(), _KEY_ID)

    with pytest.raises(KeyError):
        kms.wrap_key("alias/some-other-key", b"a-32-byte-data-encryption-key!!!")


def test_unwrap_accepts_a_kek_id_other_than_the_current_one() -> None:
    """Unlike wrap_key, unwrap_key must keep working for a DEK wrapped
    under an older key/alias -- AWS KMS's own Decrypt API resolves the
    right key from the ciphertext itself, and this adapter must not
    second-guess that by rejecting an unfamiliar kek_id."""
    kms = AwsKmsAdapter(_FakeKmsClient(), _KEY_ID)
    old_kek_id = "alias/asc-recovery-kek-2025"
    wrapped = kms.wrap_key(kms.current_kek_id(), b"a-32-byte-data-encryption-key!!!")

    # No KeyError -- unwrap_key doesn't validate kek_id against "current"
    # the way wrap_key does.
    result = kms.unwrap_key(old_kek_id, wrapped)
    assert result == b"a-32-byte-data-encryption-key!!!"


def test_the_kms_client_is_called_with_the_configured_key_id() -> None:
    calls: list[tuple[str, str]] = []

    class _RecordingClient(_FakeKmsClient):
        def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, bytes]:  # noqa: N803
            calls.append(("encrypt", KeyId))
            return super().encrypt(KeyId=KeyId, Plaintext=Plaintext)

    kms = AwsKmsAdapter(_RecordingClient(), _KEY_ID)
    kms.wrap_key(_KEY_ID, b"a-32-byte-data-encryption-key!!!")

    assert calls == [("encrypt", _KEY_ID)]
