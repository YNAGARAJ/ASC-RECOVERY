"""Stopgap `KeyManagementService` adapter for deployments without a real
cloud KMS wired up yet -- AWS KMS / Azure Key Vault adapters behind this
same port are a named, deferred gap (docs/SECURITY.md), tracked separately
from this phase since building one can't be verified without a real cloud
account.

Holds a single static KEK, derived from a secret rather than generated
in-process, so it survives process restarts -- unlike `kms_local.LocalKMS`
(dev/test-only, loses its keys the moment the process exits). Still
meaningfully weaker than a real cloud KMS: no per-operation access audit
trail at the KMS layer and no automatic rotation. The KEK's own protection
is only as good as whatever secret store hands it to `EnvSecretStore`
(Secrets Manager / Key Vault in a real deployment, per terraform/README.md)
-- this adapter never writes the key material anywhere itself.

**Deliberately incompatible with per-org encryption keys (Phase 6,
`docs/MASTER-BUILD-PROMPT-V2.md`)** -- `wrap_key` raises `KeyError` for
any `kek_id` other than its one static key, on purpose: this adapter
holds exactly one key, so there is no safe way to honor an org's
`organizations.kms_key_id` even if one is configured. Setting that
column on an org while `KMS_PROVIDER` is unset/`"env"` is an operator
misconfiguration (`docs/RUNBOOK.md`), not a state this codebase silently
tolerates or works around -- per-org keys only become real once
`KMS_PROVIDER` is `aws-kms`/`azure-keyvault` (`security/kms_aws.py`,
`security/kms_azure.py`, both of which accept any `kek_id`).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from security.kms import KeyManagementService
from security.secrets import SecretStore

_CURRENT_KEK_ID = "env-kek-v1"
_NONCE_LENGTH_BYTES = 12
_KEK_LENGTH_BYTES = 32


class EnvKMS(KeyManagementService):
    def __init__(self, secrets: SecretStore, *, secret_name: str = "PHI_ENCRYPTION_KEY") -> None:
        encoded = secrets.get_secret(secret_name)
        key = base64.b64decode(encoded)
        if len(key) != _KEK_LENGTH_BYTES:
            raise ValueError(
                f"{secret_name} must decode to exactly {_KEK_LENGTH_BYTES} bytes "
                f"(a 256-bit AES-GCM key), got {len(key)}"
            )
        self._kek = key

    def current_kek_id(self) -> str:
        return _CURRENT_KEK_ID

    def wrap_key(self, kek_id: str, dek: bytes) -> bytes:
        if kek_id != _CURRENT_KEK_ID:
            raise KeyError(kek_id)
        aesgcm = AESGCM(self._kek)
        nonce = os.urandom(_NONCE_LENGTH_BYTES)
        return nonce + aesgcm.encrypt(nonce, dek, None)

    def unwrap_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        if kek_id != _CURRENT_KEK_ID:
            raise KeyError(kek_id)
        aesgcm = AESGCM(self._kek)
        nonce, ciphertext = wrapped_dek[:_NONCE_LENGTH_BYTES], wrapped_dek[_NONCE_LENGTH_BYTES:]
        return aesgcm.decrypt(nonce, ciphertext, None)
