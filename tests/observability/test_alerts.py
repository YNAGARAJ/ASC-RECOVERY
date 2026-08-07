from __future__ import annotations

from observability.alerts import (
    EvalScoreSnapshot,
    evaluate_auth_anomaly_alert,
    evaluate_cross_tenant_probe_alert,
    evaluate_eval_regression_alert,
    evaluate_ingestion_failure_alert,
    evaluate_job_dead_lettered_alert,
    evaluate_unusual_phi_access_alert,
)


def test_ingestion_failure_alert_fires_above_threshold() -> None:
    alert = evaluate_ingestion_failure_alert(quarantined_count=3, total_count=10, max_rate=0.10)
    assert alert is not None
    assert alert.name == "ingestion_failure_rate"
    assert alert.severity == "critical"


def test_ingestion_failure_alert_silent_below_threshold() -> None:
    assert evaluate_ingestion_failure_alert(quarantined_count=1, total_count=100) is None


def test_ingestion_failure_alert_silent_with_no_ingestions() -> None:
    assert evaluate_ingestion_failure_alert(quarantined_count=0, total_count=0) is None


def test_eval_regression_alert_fires_on_recall_drop() -> None:
    current = EvalScoreSnapshot(recall=0.90, precision=0.99)
    previous = EvalScoreSnapshot(recall=1.00, precision=0.99)

    alert = evaluate_eval_regression_alert(current, previous)

    assert alert is not None
    assert alert.name == "eval_regression"


def test_eval_regression_alert_silent_within_tolerance() -> None:
    current = EvalScoreSnapshot(recall=0.995, precision=0.985)
    previous = EvalScoreSnapshot(recall=1.00, precision=1.00)

    assert evaluate_eval_regression_alert(current, previous, max_drop=0.02) is None


def test_eval_regression_alert_silent_with_no_prior_run() -> None:
    current = EvalScoreSnapshot(recall=1.0, precision=1.0)
    assert evaluate_eval_regression_alert(current, None) is None


def test_auth_anomaly_alert_fires_at_threshold() -> None:
    alert = evaluate_auth_anomaly_alert(
        actor="user-1", failed_attempts=5, window_description="in the last 15 minutes", threshold=5
    )
    assert alert is not None
    assert alert.name == "auth_anomaly"


def test_auth_anomaly_alert_silent_below_threshold() -> None:
    alert = evaluate_auth_anomaly_alert(
        actor="user-1", failed_attempts=4, window_description="in the last 15 minutes", threshold=5
    )
    assert alert is None


def test_unusual_phi_access_alert_fires_at_threshold() -> None:
    alert = evaluate_unusual_phi_access_alert(
        actor="biller-1", access_count=50, window_description="in the last hour", threshold=50
    )
    assert alert is not None
    assert alert.name == "unusual_phi_access_volume"


def test_cross_tenant_probe_alert_fires_at_threshold() -> None:
    alert = evaluate_cross_tenant_probe_alert(
        actor="user-2", not_found_count=10, window_description="in the last hour", threshold=10
    )
    assert alert is not None
    assert alert.name == "cross_tenant_probe"


def test_job_dead_lettered_alert_always_fires_no_threshold() -> None:
    alert = evaluate_job_dead_lettered_alert(
        job_id="11111111-1111-1111-1111-111111111111",
        job_type="ingest_remittance",
        facility_id="22222222-2222-2222-2222-222222222222",
        attempts=5,
    )
    assert alert.name == "job_dead_lettered"
    assert alert.severity == "critical"
    assert "5 attempts" in alert.message


def test_cross_tenant_probe_alert_silent_below_threshold() -> None:
    alert = evaluate_cross_tenant_probe_alert(
        actor="user-2", not_found_count=2, window_description="in the last hour", threshold=10
    )
    assert alert is None
