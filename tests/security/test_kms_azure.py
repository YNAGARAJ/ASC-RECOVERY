"""Tests for the Azure Key Vault adapter (F-20, docs/audit/REGISTER.md),
against a fake Key Vault crypto client injected via
`crypto_client_factory` -- no real Azure account or the real
`azure-keyvault-keys` SDK needed (pyproject.toml's `[cloud-kms]` extra,
not installed in this dev environment); matches this repo's usual
fake-client pattern for adapters that can't be verified against real
infrastructure here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from security.kms_azure import AzureKeyVaultAdapter

_CURRENT_KEY_ID = "https://asc-vault.vault.azure.net/keys/asc-kek/v2"
_OLD_KEY_ID = "https://asc-vault.vault.azure.net/keys/asc-kek/v1"


@dataclass
class _WrapResult:
    encrypted_key: bytes


@dataclass
class _UnwrapResult:
    key: bytes


class _FakeCryptoClient:
    """XOR against a fixed pad, keyed by which key_id constructed this
    client -- enough to prove the adapter routes wrap/unwrap through the
    right key_id-scoped client, not real cryptography."""

    def __init__(self, key_id: str) -> None:
        self._pad = (key_id.encode() * 8)[:64]

    def wrap_key(self, _algorithm: str, key: bytes) -> _WrapResult:
        pad = self._pad[: len(key)]
        return _WrapResult(encrypted_key=bytes(a ^ b for a, b in zip(key, pad, strict=True)))

    def unwrap_key(self, _algorithm: str, encrypted_key: bytes) -> _UnwrapResult:
        pad = self._pad[: len(encrypted_key)]
        return _UnwrapResult(key=bytes(a ^ b for a, b in zip(encrypted_key, pad, strict=True)))


def _fake_factory(calls: list[str] | None = None) -> Any:
    def factory(kek_id: str, _credential: Any) -> _FakeCryptoClient:
        if calls is not None:
            calls.append(kek_id)
        return _FakeCryptoClient(kek_id)

    return factory


def test_wrap_then_unwrap_round_trips() -> None:
    kms = AzureKeyVaultAdapter(
        credential=object(), current_key_id=_CURRENT_KEY_ID, crypto_client_factory=_fake_factory()
    )
    dek = b"a-32-byte-data-encryption-key!!!"

    wrapped = kms.wrap_key(kms.current_kek_id(), dek)

    assert kms.unwrap_key(kms.current_kek_id(), wrapped) == dek


def test_current_kek_id_is_the_pinned_version() -> None:
    kms = AzureKeyVaultAdapter(
        credential=object(), current_key_id=_CURRENT_KEY_ID, crypto_client_factory=_fake_factory()
    )
    assert kms.current_kek_id() == _CURRENT_KEY_ID


def test_wrap_with_a_kek_id_other_than_the_pinned_current_version_raises() -> None:
    kms = AzureKeyVaultAdapter(
        credential=object(), current_key_id=_CURRENT_KEY_ID, crypto_client_factory=_fake_factory()
    )

    with pytest.raises(KeyError):
        kms.wrap_key(_OLD_KEY_ID, b"a-32-byte-data-encryption-key!!!")


def test_unwrap_works_against_an_older_pinned_version_still_in_the_vault() -> None:
    """The whole reason unwrap_key doesn't validate kek_id the way
    wrap_key does: a DEK wrapped under a version from before a rotation
    must still be unwrappable as long as that version is still enabled in
    the vault -- current_key_id only pins what NEW wraps use."""
    old_adapter = AzureKeyVaultAdapter(
        credential=object(), current_key_id=_OLD_KEY_ID, crypto_client_factory=_fake_factory()
    )
    dek = b"a-32-byte-data-encryption-key!!!"
    wrapped_under_old_version = old_adapter.wrap_key(_OLD_KEY_ID, dek)

    # A fresh adapter instance, rotated to a new pinned current version --
    # simulates redeploying with a new AZURE_KEY_VAULT_KEY_ID after a
    # rotation. It must still unwrap the old payload correctly.
    rotated_adapter = AzureKeyVaultAdapter(
        credential=object(), current_key_id=_CURRENT_KEY_ID, crypto_client_factory=_fake_factory()
    )
    assert rotated_adapter.unwrap_key(_OLD_KEY_ID, wrapped_under_old_version) == dek


def test_each_call_builds_a_crypto_client_scoped_to_the_given_kek_id() -> None:
    calls: list[str] = []
    kms = AzureKeyVaultAdapter(
        credential=object(),
        current_key_id=_CURRENT_KEY_ID,
        crypto_client_factory=_fake_factory(calls),
    )
    dek = b"a-32-byte-data-encryption-key!!!"

    wrapped = kms.wrap_key(_CURRENT_KEY_ID, dek)
    kms.unwrap_key(_CURRENT_KEY_ID, wrapped)

    assert calls == [_CURRENT_KEY_ID, _CURRENT_KEY_ID]
