"""Shared cross-tenant-probe alert wiring (F-11, docs/audit/REGISTER.md)
for every direct-id-lookup route that can 404 -- findings.py and
packets.py both need the identical record-and-maybe-notify step, so it
lives here once rather than copied per file.

A cross-tenant lookup doesn't raise a distinguishable error by design
(Phase 6: no endpoint accepts a client-supplied tenant id, and a
cross-tenant lookup by id 404s exactly like a nonexistent id would --
deliberately, so the response itself never confirms another tenant's
resource exists). A burst of these from one actor is the observable
proxy signal this alert watches for -- see
`observability.alerts.evaluate_cross_tenant_probe_alert`'s own
docstring.
"""

from __future__ import annotations

from fastapi import Request

from observability.alert_state import RollingWindowCounter
from observability.alerts import evaluate_cross_tenant_probe_alert
from observability.notifications import NotificationPort

_WINDOW_DESCRIPTION = "in the last 5 minutes"


def record_not_found(request: Request, actor: str) -> None:
    tracker: RollingWindowCounter = request.app.state.not_found_tracker
    notifier: NotificationPort = request.app.state.notifier
    count = tracker.record(actor)
    alert = evaluate_cross_tenant_probe_alert(
        actor=actor, not_found_count=count, window_description=_WINDOW_DESCRIPTION
    )
    if alert is not None:
        notifier.notify(alert)
