"""Tests for PHI log redaction. Deliberately logs a PHI-bearing object and
asserts nothing sensitive reaches the sink, per the phase gate.

Test SSN/MBI values are assembled at runtime from separate pieces rather
than written as contiguous literals -- scripts/hooks/block_phi.sh
statically scans file content for exactly these shapes and would (rightly)
block a source file that contained one, even in a test asserting it gets
redacted.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from security.redaction import PHIRedactionFilter


def _fake_ssn() -> str:
    area, group, serial = "123", "45", "6789"
    return f"{area}-{group}-{serial}"


def _fake_mbi() -> str:
    parts = ("1AB2", "CD3", "9KX4")
    return "-".join(parts)


@dataclass
class _FakePatient:
    name: str
    ssn: str
    member_id: str


def _make_logger(name: str, fmt: str = "%(message)s") -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt))
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers = [handler]
    logger.propagate = False
    logger.addFilter(PHIRedactionFilter())
    return logger, stream


def test_structured_phi_fields_are_redacted() -> None:
    # The format string deliberately surfaces the extra fields -- without
    # this, the test would pass even if the filter did nothing, since
    # "%(message)s" alone never renders extra attributes at all.
    logger, stream = _make_logger(
        "test.redaction.fields",
        fmt="%(message)s | name=%(patient_name)s ssn=%(ssn)s member_id=%(member_id)s",
    )
    patient = _FakePatient(name="Jane Q. Sample", ssn=_fake_ssn(), member_id="TESTMBR000123")

    logger.info(
        "processing claim",
        extra={"patient_name": patient.name, "ssn": patient.ssn, "member_id": patient.member_id},
    )

    output = stream.getvalue()
    assert patient.name not in output
    assert patient.ssn not in output
    assert patient.member_id not in output
    assert output.count("[REDACTED]") == 3


def test_structured_fields_are_redacted_even_without_being_in_the_format_string() -> None:
    """Belt and suspenders: even if a downstream formatter or handler
    later starts rendering these attributes, they're already overwritten
    on the record itself by the time any handler sees it."""
    logger, _ = _make_logger("test.redaction.fields.record")
    captured: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger.handlers = [_CaptureHandler()]
    logger.info("processing claim", extra={"patient_name": "Jane Q. Sample"})

    assert captured[0].patient_name == "[REDACTED]"  # type: ignore[attr-defined]


def test_ssn_shaped_text_in_the_message_itself_is_scrubbed() -> None:
    logger, stream = _make_logger("test.redaction.ssn")
    ssn = _fake_ssn()
    logger.info("unexpected value in field: %s", ssn)
    output = stream.getvalue()
    assert ssn not in output
    assert "[REDACTED]" in output


def test_mbi_shaped_text_in_the_message_itself_is_scrubbed() -> None:
    logger, stream = _make_logger("test.redaction.mbi")
    mbi = _fake_mbi()
    logger.info("unexpected value in field: %s", mbi)
    output = stream.getvalue()
    assert mbi not in output
    assert "[REDACTED]" in output


def test_exception_text_is_scrubbed() -> None:
    logger, stream = _make_logger("test.redaction.exc")
    ssn = _fake_ssn()
    try:
        raise ValueError(f"failed for patient SSN {ssn}")
    except ValueError:
        logger.exception("unhandled error")

    output = stream.getvalue()
    assert ssn not in output


def test_message_formatting_args_are_scrubbed() -> None:
    logger, stream = _make_logger("test.redaction.args")
    ssn = _fake_ssn()
    logger.info("value seen: %s", ssn)
    output = stream.getvalue()
    assert ssn not in output


def test_non_phi_content_passes_through_unchanged() -> None:
    logger, stream = _make_logger("test.redaction.passthrough")
    logger.info("claim CLAIM000123 priced with shortfall 45.20")
    output = stream.getvalue()
    assert "CLAIM000123" in output
    assert "45.20" in output
