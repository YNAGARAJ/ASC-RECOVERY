"""Poll one facility's configured SFTP or S3 remittance source once,
ingesting whatever's new (F-18, docs/audit/REGISTER.md).
`SFTPPollSource`/`S3PollSource` (`ingestion/sources.py`) were fully built
and tested since Phase 5 but constructed by nothing anywhere in the
running system -- this script is the first real caller.

Deliberately a script, not a service this codebase runs continuously:
meant to be invoked periodically by whatever scheduling infrastructure a
real deployment already has (a Kubernetes CronJob, a cloud provider's
scheduled task, plain cron) -- same boundary `scripts/deploy/migrate.sh`
already draws around the deploy pipeline. No async job/queue
infrastructure is built or assumed here; see `docs/RUNBOOK.md` for a
worked scheduling example.

`seen` (`ingestion.sources`'s own in-memory duplicate-avoidance courtesy)
is intentionally NOT persisted across invocations -- the `remittances`
table stores a content hash, not the original filename, so there's
nothing to reload it from. `db.repository.record_remittance_if_new` is
the real idempotency backstop regardless (see `ingestion/sources.py`'s
own module docstring): a file already ingested gets re-fetched and
re-hashed on the next poll, then correctly recognized as a duplicate --
wasteful, never incorrect.

The real SFTP/S3 client adapters below (`_ParamikoSFTPClient`,
`_Boto3S3Client`) are untested against a real server or a real AWS
account -- no live SFTP/S3 credentials exist in this build environment,
same ceiling every other real-cloud-integration adapter in this codebase
has had since Phase 9 (real KMS adapters, the real LLM provider).

USER_ID must be a user who holds a membership resolving access to
FACILITY_ID (Phase 4, `docs/MASTER-BUILD-PROMPT-V2.md`) -- typically a
service-account user provisioned specifically for this poller, once
Phase 5's API-key/`api_service` role machinery exists; until then, any
user with a qualifying membership works, same as any other caller of
`db.access.access_session`.

Usage (SFTP):
    pip install -e ".[poller]"
    USER_ID=<uuid> FACILITY_ID=<uuid> SOURCE_KIND=sftp \\
    SFTP_HOST=... SFTP_PORT=22 SFTP_USERNAME=... SFTP_PASSWORD=... \\
    SFTP_DIR=/incoming DATABASE_URL=... PHI_ENCRYPTION_KEY=... \\
        python scripts/ingestion/poll_remittances.py

Usage (S3):
    pip install -e ".[poller]"
    USER_ID=<uuid> FACILITY_ID=<uuid> SOURCE_KIND=s3 \\
    S3_BUCKET=... S3_PREFIX=remittances/ AWS_REGION=us-east-1 \\
    DATABASE_URL=... PHI_ENCRYPTION_KEY=... \\
        python scripts/ingestion/poll_remittances.py
"""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any

from db.access import access_session
from db.base import make_engine, make_session_factory
from ingestion.pipeline import (
    ClaimFileOutcome,
    DuplicateClaimFileOutcome,
    DuplicateOutcome,
    ingest_file,
)
from ingestion.poller import PollableOutcome, PolledOutcome, poll_and_ingest
from ingestion.sources import IncomingFile, IngestionSource, S3PollSource, SFTPPollSource
from ingestion.virus_scan import EicarAwareScanner
from security.encryption import EnvelopeEncryptor
from security.kms_env import EnvKMS
from security.secrets import EnvSecretStore, SecretNotFoundError


class MissingConfigurationError(RuntimeError):
    pass


def _require(secrets: EnvSecretStore, name: str) -> str:
    try:
        return secrets.get_secret(name)
    except SecretNotFoundError as exc:
        raise MissingConfigurationError(
            f"required environment variable {name} is not set"
        ) from exc


class _ParamikoSFTPClient:
    """Adapts a real `paramiko.SFTPClient` to `ingestion.sources.SFTPClient`
    -- untested against a real SFTP server, see this module's docstring."""

    def __init__(self, sftp: Any) -> None:
        self._sftp = sftp

    def listdir(self, path: str) -> list[str]:
        result: list[str] = self._sftp.listdir(path)
        return result

    def open(self, path: str) -> bytes:
        with self._sftp.open(path, "rb") as handle:
            content: bytes = handle.read()
            return content


class _Boto3S3Client:
    """Adapts a real boto3 S3 client to `ingestion.sources.S3Client` --
    untested against a real AWS account, see this module's docstring."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def list_objects(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    def get_object(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        content: bytes = response["Body"].read()
        return content


def _build_sftp_source(secrets: EnvSecretStore) -> IngestionSource:
    # Lazy: only SOURCE_KIND=sftp needs this (pyproject.toml's [poller] extra).
    import paramiko

    host = _require(secrets, "SFTP_HOST")
    port = int(os.environ.get("SFTP_PORT", "22"))
    username = _require(secrets, "SFTP_USERNAME")
    password = _require(secrets, "SFTP_PASSWORD")
    remote_dir = _require(secrets, "SFTP_DIR")

    transport = paramiko.Transport((host, port))
    transport.connect(username=username, password=password)
    sftp = paramiko.SFTPClient.from_transport(transport)
    return SFTPPollSource(_ParamikoSFTPClient(sftp), remote_dir, set())


def _build_s3_source(secrets: EnvSecretStore) -> IngestionSource:
    # Lazy: only SOURCE_KIND=s3 needs this (pyproject.toml's [poller] extra).
    import boto3

    bucket = _require(secrets, "S3_BUCKET")
    prefix = os.environ.get("S3_PREFIX", "")
    region = os.environ.get("AWS_REGION")
    client = boto3.client("s3", region_name=region)
    return S3PollSource(_Boto3S3Client(client, bucket), prefix, set())


def _report(results: tuple[PolledOutcome, ...]) -> None:
    for result in results:
        outcome = result.outcome
        if isinstance(outcome, DuplicateOutcome):
            print(f"{result.file_name}: duplicate (remittance {outcome.remittance_id})")
        elif isinstance(outcome, DuplicateClaimFileOutcome):
            print(f"{result.file_name}: duplicate (claim file {outcome.claim_file_id})")
        elif isinstance(outcome, ClaimFileOutcome):
            print(
                f"{result.file_name}: {outcome.status} (837 claim file, "
                f"enriched={outcome.claims_enriched}, unmatched={outcome.claims_unmatched})"
            )
        else:
            print(
                f"{result.file_name}: {outcome.status} "
                f"(claims={outcome.claims_created}, findings={outcome.findings_created})"
            )


def main() -> int:
    secrets = EnvSecretStore()
    user_id = uuid.UUID(_require(secrets, "USER_ID"))
    facility_id = uuid.UUID(_require(secrets, "FACILITY_ID"))
    source_kind = _require(secrets, "SOURCE_KIND")
    database_url = _require(secrets, "DATABASE_URL")
    _require(secrets, "PHI_ENCRYPTION_KEY")  # validated eagerly by EnvKMS below
    encryptor = EnvelopeEncryptor(EnvKMS(secrets))

    if source_kind == "sftp":
        source = _build_sftp_source(secrets)
    elif source_kind == "s3":
        source = _build_s3_source(secrets)
    else:
        raise MissingConfigurationError(
            f"SOURCE_KIND must be 'sftp' or 's3', got {source_kind!r}"
        )

    session_factory = make_session_factory(make_engine(database_url))
    scanner = EicarAwareScanner()

    def ingest_one(file: IncomingFile) -> PollableOutcome:
        with access_session(session_factory, user_id) as session:
            return ingest_file(
                session,
                facility_id,
                content=file.content,
                source=file.source,
                uploaded_by=f"{source_kind}-poller",
                scanner=scanner,
                encryptor=encryptor,
            )

    results = poll_and_ingest(source, ingest_one)
    _report(results)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MissingConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
