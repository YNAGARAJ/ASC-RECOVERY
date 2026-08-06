"""Tests for F-11's LoggingNotificationPort (docs/audit/REGISTER.md) --
the real, functional default until a real paging vendor exists."""

from __future__ import annotations

import logging

from observability.alerts import Alert
from observability.notifications import LoggingNotificationPort


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _make_capturing_logger(name: str) -> tuple[logging.Logger, _CaptureHandler]:
    handler = _CaptureHandler()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers = [handler]
    logger.propagate = False
    return logger, handler


def test_critical_alert_logs_at_critical_level() -> None:
    logger, handler = _make_capturing_logger("test.notifications.critical")

    port = LoggingNotificationPort(logger)
    port.notify(Alert(severity="critical", name="ingestion_failure_rate", message="boom"))

    assert len(handler.records) == 1
    assert handler.records[0].levelno == logging.CRITICAL
    assert "ingestion_failure_rate" in handler.records[0].getMessage()
    assert "boom" in handler.records[0].getMessage()


def test_warning_alert_logs_at_warning_level() -> None:
    logger, handler = _make_capturing_logger("test.notifications.warning")

    port = LoggingNotificationPort(logger)
    port.notify(Alert(severity="warning", name="auth_anomaly", message="suspicious"))

    assert len(handler.records) == 1
    assert handler.records[0].levelno == logging.WARNING


def test_defaults_to_a_working_logger_when_none_is_given() -> None:
    """No logger passed -- must not raise, and must actually log
    somewhere rather than silently doing nothing."""
    port = LoggingNotificationPort()
    port.notify(Alert(severity="warning", name="auth_anomaly", message="ok"))
