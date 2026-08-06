"""Tests for F-10's structural PHI log redaction (docs/audit/REGISTER.md)
-- configure_logging installs PHIRedactionFilter on a root-logger
handler, not on any one named logger, so a brand-new logger that never
explicitly attaches the filter itself still gets redacted.

SSN-shaped test values are assembled at runtime, same workaround used in
tests/security/test_redaction.py, so this file's own source never
contains an SSN-shaped literal for scripts/hooks/block_phi.sh to trip on.
"""

from __future__ import annotations

import io
import logging

from observability.logging_config import configure_logging
from security.redaction import PHIRedactionFilter


def _fake_ssn() -> str:
    area, group, serial = "123", "45", "6789"
    return f"{area}-{group}-{serial}"


def test_installs_the_filter_on_a_handler_attached_to_the_root_logger() -> None:
    stream = io.StringIO()
    handler = configure_logging(stream=stream)
    try:
        assert handler in logging.getLogger().handlers
        assert any(isinstance(f, PHIRedactionFilter) for f in handler.filters)
    finally:
        logging.getLogger().removeHandler(handler)


def test_a_logger_that_never_attached_the_filter_itself_is_still_redacted() -> None:
    """The actual F-10 proof: a logger name used nowhere else in this
    codebase, given no filter of its own, is still scrubbed -- because
    the filter lives on the root handler every logger propagates
    through by default, not because this specific logger opted in."""
    stream = io.StringIO()
    handler = configure_logging(stream=stream)
    logger = logging.getLogger("test.f10.never_directly_filtered")
    try:
        ssn = _fake_ssn()
        logger.info("unexpected value: %s", ssn)

        output = stream.getvalue()
        assert ssn not in output
        assert "[REDACTED]" in output
    finally:
        logging.getLogger().removeHandler(handler)


def test_root_level_is_lowered_so_info_records_actually_reach_the_handler() -> None:
    """Without this, api.request's request-started INFO log
    (api/request_context.py) would be silently dropped -- an unconfigured
    root logger defaults to WARNING, one level above INFO."""
    stream = io.StringIO()
    handler = configure_logging(stream=stream, level=logging.INFO)
    logger = logging.getLogger("test.f10.info_level")
    try:
        logger.info("request started")
        assert "request started" in stream.getvalue()
    finally:
        logging.getLogger().removeHandler(handler)
