"""Application-level envelope encryption for PHI columns. AES-256-GCM, in
addition to disk encryption -- the 2026 HIPAA Security Rule makes this
required, not "addressable" (see docs/SECURITY.md).

Each value gets its own randomly generated 256-bit DEK (data encryption
key), used exactly once. The DEK itself is "wrapped" (encrypted) by a KEK
(key-encryption key) obtained from a KeyManagementService port -- this is
what makes key rotation cheap: rotating the KEK only means re-wrapping the
small DEK, never touching the (potentially large) ciphertext.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from security.kms import KeyManagementService

_DEK_BITS = 256
_NONCE_LENGTH_BYTES = 12


class DecryptionError(Exception):
    """Wrong key, an unknown kek_id, or the ciphertext/tag has been
    tampered with (GCM authentication failure)."""


@dataclass(frozen=True, slots=True)
class EncryptedPayload:
    kek_id: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


class EnvelopeEncryptor:
    def __init__(self, kms: KeyManagementService) -> None:
        self._kms = kms

    def encrypt(self, plaintext: bytes) -> EncryptedPayload:
        kek_id = self._kms.current_kek_id()
        dek = AESGCM.generate_key(bit_length=_DEK_BITS)
        nonce = os.urandom(_NONCE_LENGTH_BYTES)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, None)
        wrapped_dek = self._kms.wrap_key(kek_id, dek)
        return EncryptedPayload(
            kek_id=kek_id, wrapped_dek=wrapped_dek, nonce=nonce, ciphertext=ciphertext
        )

    def decrypt(self, payload: EncryptedPayload) -> bytes:
        try:
            dek = self._kms.unwrap_key(payload.kek_id, payload.wrapped_dek)
            return AESGCM(dek).decrypt(payload.nonce, payload.ciphertext, None)
        except (InvalidTag, KeyError) as exc:
            raise DecryptionError(
                "payload failed to decrypt -- wrong key, unknown kek_id, or tampered ciphertext"
            ) from exc

    def rotate_kek(self, payload: EncryptedPayload, new_kek_id: str) -> EncryptedPayload:
        """Re-wraps the DEK under a new KEK and returns a new payload. The
        bulk ciphertext and nonce are byte-for-byte unchanged -- only the
        small wrapped_dek and kek_id fields differ. `payload` itself is
        untouched (EncryptedPayload is frozen), so it still decrypts under
        its original KEK as long as that KEK remains in the KMS."""
        dek = self._kms.unwrap_key(payload.kek_id, payload.wrapped_dek)
        new_wrapped_dek = self._kms.wrap_key(new_kek_id, dek)
        return EncryptedPayload(
            kek_id=new_kek_id,
            wrapped_dek=new_wrapped_dek,
            nonce=payload.nonce,
            ciphertext=payload.ciphertext,
        )
