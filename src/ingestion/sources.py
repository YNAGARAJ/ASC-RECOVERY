"""Ingestion source port + adapters.

Same port/adapter shape as security/kms.py: a `Protocol` per source kind, so
a new source is a new adapter rather than a pipeline rewrite. `SFTPClient`
and `S3Client` below are minimal structural Protocols -- a real
`paramiko.SFTPClient` or `boto3` S3 client satisfies them without this
project taking on either as a hard dependency, mirroring how `EnvelopeEncryptor`
works against any real KMS without depending on a specific cloud SDK.

Idempotency is not this module's job: `db.repository.record_remittance_if_new`
is the real backstop (content-hash based). The "already seen" tracking here
is a courtesy to avoid re-fetching/re-listing the same remote file on every
poll, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IncomingFile:
    name: str
    content: bytes
    source: str


class IngestionSource(Protocol):
    def poll(self) -> tuple[IncomingFile, ...]: ...


class UploadSource:
    """Wraps a single already-in-memory file -- what a direct upload (a
    future Phase 6 API endpoint) constructs. `poll()` returns it exactly
    once; a fresh `UploadSource` is constructed per upload."""

    def __init__(self, name: str, content: bytes) -> None:
        self._name = name
        self._content = content

    def poll(self) -> tuple[IncomingFile, ...]:
        return (IncomingFile(name=self._name, content=self._content, source="upload"),)


class SFTPClient(Protocol):
    def listdir(self, path: str) -> list[str]: ...
    def open(self, path: str) -> bytes: ...


class SFTPPollSource:
    """Polls a remote directory for files not already seen. `seen` is
    injected so the caller controls persistence of what's already been
    fetched (e.g. backed by a set reloaded from the remittances table's
    known filenames) -- this class holds no state of its own beyond one
    poll call."""

    def __init__(self, client: SFTPClient, remote_dir: str, seen: set[str]) -> None:
        self._client = client
        self._remote_dir = remote_dir
        self._seen = seen

    def poll(self) -> tuple[IncomingFile, ...]:
        files: list[IncomingFile] = []
        for name in self._client.listdir(self._remote_dir):
            if name in self._seen:
                continue
            content = self._client.open(f"{self._remote_dir}/{name}")
            files.append(IncomingFile(name=name, content=content, source="sftp"))
            self._seen.add(name)
        return tuple(files)


class S3Client(Protocol):
    def list_objects(self, prefix: str) -> list[str]: ...
    def get_object(self, key: str) -> bytes: ...


class S3PollSource:
    """Same shape as SFTPPollSource, against a minimal S3-compatible client
    Protocol -- works against AWS S3, and equally against any S3-compatible
    object store on another cloud (CLAUDE.md rule 7: cloud-agnostic)."""

    def __init__(self, client: S3Client, prefix: str, seen: set[str]) -> None:
        self._client = client
        self._prefix = prefix
        self._seen = seen

    def poll(self) -> tuple[IncomingFile, ...]:
        files: list[IncomingFile] = []
        for key in self._client.list_objects(self._prefix):
            if key in self._seen:
                continue
            content = self._client.get_object(key)
            files.append(IncomingFile(name=key, content=content, source="s3"))
            self._seen.add(key)
        return tuple(files)
