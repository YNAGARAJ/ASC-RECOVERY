"""Secret management port. Nothing in env *files* is committed -- per
CLAUDE.md and the phase prompt, `EnvSecretStore` reads from the process
environment at runtime only, never from a checked-in `.env`. Real
deployments plug in a Vault / AWS Secrets Manager / Azure Key Vault / GCP
Secret Manager adapter behind this same port (Phase 9/11); `EnvSecretStore`
is the dev/test adapter until then.
"""

from __future__ import annotations

import os
from typing import Protocol


class SecretNotFoundError(KeyError):
    pass


class SecretStore(Protocol):
    def get_secret(self, name: str) -> str: ...


class EnvSecretStore:
    """Reads secrets from process environment variables, optionally under
    a name prefix to namespace them. Dev/test only: env vars are visible
    to anything that can read the process environment (a crash dump, a
    child process, `ps -eww`), which is not an acceptable posture for real
    secrets in production."""

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def get_secret(self, name: str) -> str:
        env_name = f"{self._prefix}{name}"
        value = os.environ.get(env_name)
        if value is None:
            raise SecretNotFoundError(env_name)
        return value
