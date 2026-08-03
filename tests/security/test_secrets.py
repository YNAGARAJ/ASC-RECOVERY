"""Tests for the secret management port's dev/test adapter."""

from __future__ import annotations

import pytest

from security.secrets import EnvSecretStore, SecretNotFoundError


def test_get_secret_reads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "s3cr3t-value")
    store = EnvSecretStore()
    assert store.get_secret("MY_SECRET") == "s3cr3t-value"


def test_get_secret_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOES_NOT_EXIST", raising=False)
    store = EnvSecretStore()
    with pytest.raises(SecretNotFoundError):
        store.get_secret("DOES_NOT_EXIST")


def test_get_secret_applies_the_configured_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASC_DB_PASSWORD", "hunter2")
    store = EnvSecretStore(prefix="ASC_")
    assert store.get_secret("DB_PASSWORD") == "hunter2"


def test_get_secret_without_prefix_ignores_prefixed_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASC_DB_PASSWORD", "hunter2")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    store = EnvSecretStore()
    with pytest.raises(SecretNotFoundError):
        store.get_secret("DB_PASSWORD")
