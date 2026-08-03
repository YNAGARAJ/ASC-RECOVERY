"""Key Management Service port.

Abstracted so the same `EnvelopeEncryptor` (encryption.py) works against
AWS KMS, Azure Key Vault, GCP KMS, or Vault without any caller-side change
-- per CLAUDE.md rule 7 (cloud-agnostic, no proprietary managed service
without an equivalent elsewhere). See kms_local.py for the dev/test
adapter used until Phase 9 wires a real cloud KMS behind this same port.

Adapters are expected to raise `KeyError` (or a subclass) from
`wrap_key`/`unwrap_key` when `kek_id` is not one they know about --
`EnvelopeEncryptor.decrypt` treats that the same as a failed decryption.
"""

from __future__ import annotations

from typing import Protocol


class KeyManagementService(Protocol):
    """A KEK (key-encryption key) store. Never sees plaintext PHI -- only
    ever wraps/unwraps small (32-byte) data-encryption keys."""

    def current_kek_id(self) -> str:
        """The KEK id new encryptions should be wrapped under."""
        ...

    def wrap_key(self, kek_id: str, dek: bytes) -> bytes: ...

    def unwrap_key(self, kek_id: str, wrapped_dek: bytes) -> bytes: ...
