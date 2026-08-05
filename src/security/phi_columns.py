"""Serialization glue between `security.encryption.EnvelopeEncryptor` and a
single `Text` database column, for PHI fields that don't warrant a whole
extra table (`claims.patient_name_encrypted`, `claims.patient_member_id_encrypted`).

Not a general-purpose serialization framework -- exactly the two functions
ingestion/apply.py (write path) and api/repository.py (read path) need.
"""

from __future__ import annotations

import base64
import json

from security.encryption import EncryptedPayload, EnvelopeEncryptor


def encrypt_phi_field(encryptor: EnvelopeEncryptor, plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    payload = encryptor.encrypt(plaintext.encode("utf-8"))
    return json.dumps(
        {
            "kek_id": payload.kek_id,
            "wrapped_dek": base64.b64encode(payload.wrapped_dek).decode("ascii"),
            "nonce": base64.b64encode(payload.nonce).decode("ascii"),
            "ciphertext": base64.b64encode(payload.ciphertext).decode("ascii"),
        }
    )


def decrypt_phi_field(encryptor: EnvelopeEncryptor, serialized: str | None) -> str | None:
    if serialized is None:
        return None
    raw = json.loads(serialized)
    payload = EncryptedPayload(
        kek_id=raw["kek_id"],
        wrapped_dek=base64.b64decode(raw["wrapped_dek"]),
        nonce=base64.b64decode(raw["nonce"]),
        ciphertext=base64.b64decode(raw["ciphertext"]),
    )
    return encryptor.decrypt(payload).decode("utf-8")
