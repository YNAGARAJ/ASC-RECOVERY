"""Alert evaluation logic for the five conditions the Phase 8 prompt
requires: ingestion failure, eval regression, auth anomaly, unusual PHI
access volume, cross-tenant query attempt.

Every function here is pure -- it takes already-collected counts/records
and returns an `Alert` or `None`. Wiring these to a real notification
channel (PagerDuty, Slack, email) is deployment-specific and deferred,
same as this codebase's other real-integration deferrals (real cloud KMS
adapters, a real LLM provider, a real antivirus engine): the detection
*logic* is what's genuinely built and tested here; a specific paging
vendor is a Phase 9/10 deployment decision, not an engineering one.

`evaluate_eval_regression_alert` takes a plain `EvalScoreSnapshot`
rather than `evals.history.EvalRunRecord` -- `evals/` is evaluation
tooling that depends on `src/`, not the other way around, so this module
doesn't import from it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Alert:
    severity: str  # "warning" | "critical"
    name: str
    message: str


@dataclass(frozen=True, slots=True)
class EvalScoreSnapshot:
    recall: float
    precision: float


def evaluate_ingestion_failure_alert(
    *, quarantined_count: int, total_count: int, max_rate: float = 0.10
) -> Alert | None:
    if total_count == 0:
        return None
    rate = quarantined_count / total_count
    if rate <= max_rate:
        return None
    return Alert(
        severity="critical",
        name="ingestion_failure_rate",
        message=(
            f"{quarantined_count}/{total_count} remittances quarantined "
            f"({rate:.0%}), above the {max_rate:.0%} threshold"
        ),
    )


def evaluate_eval_regression_alert(
    current: EvalScoreSnapshot, previous: EvalScoreSnapshot | None, *, max_drop: float = 0.02
) -> Alert | None:
    if previous is None:
        return None
    recall_drop = previous.recall - current.recall
    precision_drop = previous.precision - current.precision
    if recall_drop <= max_drop and precision_drop <= max_drop:
        return None
    return Alert(
        severity="critical",
        name="eval_regression",
        message=(
            f"recall {previous.recall:.1%} -> {current.recall:.1%}, "
            f"precision {previous.precision:.1%} -> {current.precision:.1%} "
            f"(threshold: no more than {max_drop:.0%} drop in either)"
        ),
    )


def evaluate_auth_anomaly_alert(
    *, actor: str, failed_attempts: int, window_description: str, threshold: int = 5
) -> Alert | None:
    if failed_attempts < threshold:
        return None
    return Alert(
        severity="warning",
        name="auth_anomaly",
        message=(
            f"{actor!r} had {failed_attempts} failed authentication attempts "
            f"{window_description} (threshold: {threshold})"
        ),
    )


def evaluate_unusual_phi_access_alert(
    *, actor: str, access_count: int, window_description: str, threshold: int = 50
) -> Alert | None:
    if access_count < threshold:
        return None
    return Alert(
        severity="warning",
        name="unusual_phi_access_volume",
        message=(
            f"{actor!r} accessed PHI-bearing records {access_count} times "
            f"{window_description} (threshold: {threshold})"
        ),
    )


def evaluate_cross_tenant_probe_alert(
    *, actor: str, not_found_count: int, window_description: str, threshold: int = 10
) -> Alert | None:
    """Cross-tenant lookups don't raise a distinguishable error by design
    (Phase 6: no endpoint accepts a client-supplied tenant id, and a
    cross-tenant lookup by id 404s exactly like a nonexistent id would --
    deliberately, so the response itself never confirms another tenant's
    resource exists). A burst of id-lookup misses from one actor is the
    observable proxy signal for either enumeration/probing or a client
    bug, and is worth surfacing either way."""
    if not_found_count < threshold:
        return None
    return Alert(
        severity="warning",
        name="cross_tenant_probe",
        message=(
            f"{actor!r} triggered {not_found_count} not-found responses on "
            f"direct-id lookups {window_description} (threshold: {threshold})"
        ),
    )
