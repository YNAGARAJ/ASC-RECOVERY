"""Where an evaluated `Alert` (observability.alerts) actually goes (F-11,
docs/audit/REGISTER.md). A real paging vendor (PagerDuty/Slack/email) is
a deployment decision, deferred the same way this codebase defers every
other real-vendor integration (real cloud KMS adapters, a real LLM
provider, a real antivirus engine) -- `LoggingNotificationPort` is the
real default until one exists: genuinely functional (an alert really is
emitted, really is visible in whatever log aggregation already exists,
and goes through the same PHI-redaction path as every other log line in
this process, F-10), just not paging anyone yet.
"""

from __future__ import annotations

import logging
from typing import Protocol

from observability.alerts import Alert


class NotificationPort(Protocol):
    def notify(self, alert: Alert) -> None: ...


class LoggingNotificationPort:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("observability.alerts")

    def notify(self, alert: Alert) -> None:
        level = logging.CRITICAL if alert.severity == "critical" else logging.WARNING
        self._logger.log(level, "ALERT[%s] %s", alert.name, alert.message)
