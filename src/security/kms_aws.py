"""AWS KMS adapter (F-20, docs/audit/REGISTER.md) behind the
`KeyManagementService` port -- untested against a real AWS account, no
live credentials exist in this build environment, same disclosure as
every other real-cloud-integration adapter in this codebase (real cloud
KMS adapters were a named, deferred gap since Phase 9; see
`docs/SECURITY.md`).

Configure `key_id` as a KMS *alias* (e.g. `alias/asc-recovery-kek`), not
a raw key id, if you want AWS's own automatic annual key rotation to
take effect with zero redeploys: KMS keeps an alias pointed at whatever
the current backing key material is, and its `Decrypt` API resolves the
right key from the ciphertext blob's own embedded metadata regardless of
which key version originally encrypted it -- so `unwrap_key` below never
needs to track key versions itself the way the Azure adapter does.

**`wrap_key` accepts any `kek_id`, not just this adapter's own configured
default** (Phase 6, per-org/BYOK encryption keys,
`docs/MASTER-BUILD-PROMPT-V2.md`) -- real AWS KMS's `Encrypt` API already
takes an arbitrary `KeyId` per call, so restricting this adapter to only
ever wrap under its own `key_id` was a self-imposed limitation, not a
reflection of anything the cloud API requires. `current_kek_id()` still
returns this adapter's configured default, for callers (an org with no
`organizations.kms_key_id` of its own) that don't have a more specific
key to ask for -- `EnvelopeEncryptor.encrypt`'s own docstring covers the
default-vs-explicit-kek_id split."""

from __future__ import annotations

from typing import Any

from security.kms import KeyManagementService


class AwsKmsAdapter(KeyManagementService):
    def __init__(self, kms_client: Any, key_id: str) -> None:
        self._kms = kms_client
        self._key_id = key_id

    def current_kek_id(self) -> str:
        return self._key_id

    def wrap_key(self, kek_id: str, dek: bytes) -> bytes:
        response = self._kms.encrypt(KeyId=kek_id, Plaintext=dek)
        result: bytes = response["CiphertextBlob"]
        return result

    def unwrap_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        # KeyId here is an optional integrity check for KMS's Decrypt API,
        # not required for correctness -- the ciphertext blob itself
        # carries what actually decrypts it, so this works for any kek_id
        # a payload was ever wrapped under, including one from before a
        # key rotation.
        response = self._kms.decrypt(CiphertextBlob=wrapped_dek, KeyId=kek_id)
        result: bytes = response["Plaintext"]
        return result


def build_aws_kms_adapter(*, key_id: str, region: str | None) -> AwsKmsAdapter:
    """Lazy `boto3` import -- pyproject.toml's `[cloud-kms]` extra, not a
    hard dependency of the main application."""
    import boto3

    client = boto3.client("kms", region_name=region)
    return AwsKmsAdapter(client, key_id)
