"""Ingestion source adapters, proven against fake in-memory clients
implementing the minimal SFTPClient/S3Client Protocols -- no real network
or server needed, unlike the Postgres-backed persistence layer."""

from __future__ import annotations

from ingestion.sources import S3PollSource, SFTPPollSource, UploadSource


def test_upload_source_returns_the_wrapped_file_once() -> None:
    source = UploadSource("remit.835", b"ISA*...")

    files = source.poll()

    assert len(files) == 1
    assert files[0].name == "remit.835"
    assert files[0].content == b"ISA*..."
    assert files[0].source == "upload"


class _FakeSFTPClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def listdir(self, path: str) -> list[str]:
        return sorted(self._files)

    def open(self, path: str) -> bytes:
        name = path.rsplit("/", 1)[-1]
        return self._files[name]


def test_sftp_poll_source_returns_only_unseen_files() -> None:
    client = _FakeSFTPClient({"a.835": b"AAA", "b.835": b"BBB"})
    seen: set[str] = {"a.835"}
    source = SFTPPollSource(client, "/incoming", seen)

    files = source.poll()

    assert len(files) == 1
    assert files[0].name == "b.835"
    assert files[0].content == b"BBB"
    assert files[0].source == "sftp"


def test_sftp_poll_source_does_not_resurface_a_file_on_the_next_poll() -> None:
    client = _FakeSFTPClient({"a.835": b"AAA"})
    seen: set[str] = set()
    source = SFTPPollSource(client, "/incoming", seen)

    first = source.poll()
    second = source.poll()

    assert len(first) == 1
    assert len(second) == 0


class _FakeS3Client:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def list_objects(self, prefix: str) -> list[str]:
        return sorted(k for k in self._objects if k.startswith(prefix))

    def get_object(self, key: str) -> bytes:
        return self._objects[key]


def test_s3_poll_source_returns_only_unseen_objects() -> None:
    client = _FakeS3Client({"remits/a.835": b"AAA", "remits/b.835": b"BBB"})
    seen: set[str] = {"remits/a.835"}
    source = S3PollSource(client, "remits/", seen)

    files = source.poll()

    assert len(files) == 1
    assert files[0].name == "remits/b.835"
    assert files[0].content == b"BBB"
    assert files[0].source == "s3"
