"""Pure round-trip tests for src/jobs/payload.py -- no DB, no crypto."""

from __future__ import annotations

from jobs.payload import build_ingestion_payload, parse_ingestion_payload


def test_round_trips_content_and_source() -> None:
    content = b"ISA*00*          *00*          ~"
    serialized = build_ingestion_payload(content, source="upload")

    parsed_content, parsed_source = parse_ingestion_payload(serialized)

    assert parsed_content == content
    assert parsed_source == "upload"


def test_round_trips_binary_content_with_no_valid_utf8() -> None:
    content = bytes(range(256))
    serialized = build_ingestion_payload(content, source="sftp")

    parsed_content, parsed_source = parse_ingestion_payload(serialized)

    assert parsed_content == content
    assert parsed_source == "sftp"


def test_serialized_form_is_plain_json_no_raw_bytes() -> None:
    serialized = build_ingestion_payload(b"hello", source="s3")
    assert isinstance(serialized, str)
    assert '"source": "s3"' in serialized
