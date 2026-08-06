"""Azure Key Vault adapter (F-20, docs/audit/REGISTER.md) behind the
`KeyManagementService` port -- untested against a real Azure account, no
live credentials exist in this build environment, same disclosure as
every other real-cloud-integration adapter in this codebase (real cloud
KMS adapters were a named, deferred gap since Phase 9; see
`docs/SECURITY.md`).

Unlike AWS KMS aliases, an Azure Key Vault key *version* is a distinct
cryptographic object -- rotating the key (the module's own
`rotation_policy`, `terraform/modules/azure/secrets_and_kms.tf`) creates
a new version, and a `CryptographyClient` scoped to a specific version
can only wrap/unwrap against that exact version. `current_kek_id` here
is therefore a *pinned* version id, not "whatever's current right now":
picking up a rotated key means deploying with a new
`AZURE_KEY_VAULT_KEY_ID` (and, to re-wrap already-encrypted data under
it, an explicit `EnvelopeEncryptor.rotate_kek()` pass) -- there is no
safe way to resolve "latest version" automatically at both wrap time and
unwrap time without risking an unwrap against the wrong version's key
material. `wrap_key` enforces that every new DEK is wrapped only under
the pinned current version; `unwrap_key` intentionally does not -- it
must keep working for a DEK wrapped under an older version that is still
enabled in the vault, which is exactly what `kek_id` being carried
per-payload (`security.encryption.EncryptedPayload`) is for.

`crypto_client_factory` exists so tests can inject a fake Key Vault
client instead of needing the real `azure-keyvault-keys` SDK installed
(pyproject.toml's `[cloud-kms]` extra) -- `build_azure_keyvault_adapter`
below is the only real caller, using the real SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from security.kms import KeyManagementService

# The literal value of azure.keyvault.keys.crypto.KeyWrapAlgorithm.rsa_oaep_256
# -- Azure SDK enums of this kind subclass `str`, so passing the raw value
# is accepted identically and lets this module avoid importing the enum
# itself just to reference one member.
_WRAP_ALGORITHM = "RSA-OAEP-256"


def _real_crypto_client(kek_id: str, credential: Any) -> Any:
    from azure.keyvault.keys.crypto import CryptographyClient

    return CryptographyClient(kek_id, credential)


class AzureKeyVaultAdapter(KeyManagementService):
    def __init__(
        self,
        credential: Any,
        current_key_id: str,
        *,
        crypto_client_factory: Callable[[str, Any], Any] = _real_crypto_client,
    ) -> None:
        self._credential = credential
        self._current_key_id = current_key_id
        self._crypto_client_factory = crypto_client_factory

    def current_kek_id(self) -> str:
        return self._current_key_id

    def wrap_key(self, kek_id: str, dek: bytes) -> bytes:
        if kek_id != self._current_key_id:
            raise KeyError(kek_id)
        client = self._crypto_client_factory(kek_id, self._credential)
        result = client.wrap_key(_WRAP_ALGORITHM, dek)
        wrapped: bytes = result.encrypted_key
        return wrapped

    def unwrap_key(self, kek_id: str, wrapped_dek: bytes) -> bytes:
        client = self._crypto_client_factory(kek_id, self._credential)
        result = client.unwrap_key(_WRAP_ALGORITHM, wrapped_dek)
        unwrapped: bytes = result.key
        return unwrapped


def build_azure_keyvault_adapter(*, key_id: str) -> AzureKeyVaultAdapter:
    """Lazy `azure-identity` import -- pyproject.toml's `[cloud-kms]`
    extra, not a hard dependency of the main application.
    `DefaultAzureCredential` resolves to whatever the deployment
    environment already provides (managed identity in a real Azure
    deployment; `az login` locally) -- never a static secret this
    codebase stores itself."""
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return AzureKeyVaultAdapter(credential, key_id)
