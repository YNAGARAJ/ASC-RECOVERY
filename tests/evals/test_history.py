"""Eval run history: append/load round-trip, regression detection --
fully file-based against a tmp_path fixture, no DB, no dependency on live
anything."""

from __future__ import annotations

from pathlib import Path

from evals.history import EvalRunRecord, append_eval_run, detect_eval_regression, load_eval_history


def _record(
    recall: float, precision: float, *, recorded_at: str = "2026-01-01T00:00:00+00:00"
) -> EvalRunRecord:
    return EvalRunRecord(
        recorded_at=recorded_at,
        recall=recall,
        precision=precision,
        root_cause_accuracy=1.0,
        dollar_accuracy=1.0,
        passed=True,
    )


def test_load_history_returns_empty_list_when_file_does_not_exist(tmp_path: Path) -> None:
    assert load_eval_history(path=tmp_path / "does_not_exist.jsonl") == []


def test_append_then_load_round_trips(tmp_path: Path) -> None:
    history_path = tmp_path / "runs.jsonl"
    record = _record(1.0, 0.99)

    append_eval_run(record, path=history_path)
    loaded = load_eval_history(path=history_path)

    assert loaded == [record]


def test_multiple_appends_accumulate_in_order(tmp_path: Path) -> None:
    history_path = tmp_path / "runs.jsonl"
    first = _record(1.0, 1.0, recorded_at="2026-01-01T00:00:00+00:00")
    second = _record(0.95, 0.99, recorded_at="2026-01-02T00:00:00+00:00")

    append_eval_run(first, path=history_path)
    append_eval_run(second, path=history_path)

    assert load_eval_history(path=history_path) == [first, second]


def test_detect_regression_fires_when_recall_drops() -> None:
    history = [_record(1.0, 0.99), _record(0.90, 0.99)]
    alert = detect_eval_regression(history)
    assert alert is not None
    assert alert.name == "eval_regression"


def test_detect_regression_silent_with_stable_scores() -> None:
    history = [_record(1.0, 0.99), _record(1.0, 0.99)]
    assert detect_eval_regression(history) is None


def test_detect_regression_silent_with_fewer_than_two_runs() -> None:
    assert detect_eval_regression([_record(1.0, 0.99)]) is None
    assert detect_eval_regression([]) is None
