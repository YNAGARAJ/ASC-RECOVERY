"""Dev/test-only KeyManagementService adapter.

Holds KEKs in memory (process lifetime only) and wraps DEKs with
AES-256-GCM under each KEK. **Not for production** -- a real deployment
must use AWS KMS, Azure Key Vault, GCP KMS, or Vault (Phase 9). This exists
so encryption.py and its rotation logic are fully testable without a cloud
account.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from security.kms import KeyManagementService

_NONCE_LENGTH_BYTES = 12
_KEK_BITS = 256


class LocalKMS(KeyManagementService):
    def __init__(self) -> None:
        self._keks: dict[str, bytes] = {}
        self._current_kek_id: str | None = None

    def generate_kek(self, kek_id: str) -> None:
        """Creates a new KEK and makes it the current one -- simulates
        rotation: old KEKs stay in the store so payloads wrapped under them
        can still be unwrapped until they're explicitly re-wrapped."""
        if kek_id in self._keks:
            raise ValueError(f"KEK {kek_id!r} already exists")
        self._keks[kek_id] = AESGCM.generate_key(bit_length=_KEK_BITS)
        self._current_kek_id = kek_id

    def current_kek_id(self) -> str:
        if self._current_kek_id is None:
            raise RuntimeError("no KEK has been generated yet -- call generate_kek() first")
        return self._current_kek_id

    def wrap_key(self, kek_id: str, dek: bytes) -> bytes:
        aesgcm = AESGCM(self._keks[kek_id])
        nonce = os.urandom(_NONCE_LENGTH_BYTES)
        return nonce + aesgcm.encrypt(nonce, dek, None)

    def unwrap_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        aesgcm = AESGCM(self._keks[kek_id])
        nonce, ciphertext = wrapped_dek[:_NONCE_LENGTH_BYTES], wrapped_dek[_NONCE_LENGTH_BYTES:]
        return aesgcm.decrypt(nonce, ciphertext, None)
