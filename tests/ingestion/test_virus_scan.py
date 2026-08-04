from __future__ import annotations

from ingestion.virus_scan import EICAR_TEST_STRING, EicarAwareScanner


def test_eicar_string_is_flagged() -> None:
    scanner = EicarAwareScanner()

    result = scanner.scan(b"some file preamble\n" + EICAR_TEST_STRING + b"\ntrailer")

    assert result.clean is False
    assert "EICAR" in result.detail


def test_clean_content_is_not_flagged() -> None:
    scanner = EicarAwareScanner()

    result = scanner.scan(b"ISA*00*...ordinary 835 content...~")

    assert result.clean is True
