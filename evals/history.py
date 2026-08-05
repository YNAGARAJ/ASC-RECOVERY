"""Persists eval run results across invocations of `make eval`, so "eval
scores over time" (Phase 8) and eval-regression detection have something
to look at.

A JSONL file, not a DB table: eval runs are a build/CI quality signal,
not tenant-scoped customer data, so this stays decoupled from Postgres
availability -- `make eval` must keep working in CI without a database.

Scores are stored as `float`, not `Decimal` -- same narrow, documented
exception as `observability.metrics`: this is a telemetry/reporting
record, not the gate computation itself (`evals.run.ScoreResult` stays
`Decimal` throughout; only the persisted snapshot crosses into `float`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from observability.alerts import Alert, EvalScoreSnapshot, evaluate_eval_regression_alert

DEFAULT_HISTORY_PATH = Path(__file__).parent / "history" / "runs.jsonl"


@dataclass(frozen=True, slots=True)
class EvalRunRecord:
    recorded_at: str  # ISO-8601 UTC
    recall: float
    precision: float
    root_cause_accuracy: float
    dollar_accuracy: float
    passed: bool


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def append_eval_run(record: EvalRunRecord, *, path: Path = DEFAULT_HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as history_file:
        history_file.write(json.dumps(asdict(record)) + "\n")


def load_eval_history(*, path: Path = DEFAULT_HISTORY_PATH) -> list[EvalRunRecord]:
    if not path.exists():
        return []
    records: list[EvalRunRecord] = []
    with path.open(encoding="utf-8") as history_file:
        for line in history_file:
            line = line.strip()
            if not line:
                continue
            records.append(EvalRunRecord(**json.loads(line)))
    return records


def detect_eval_regression(
    history: Sequence[EvalRunRecord], *, max_drop: float = 0.02
) -> Alert | None:
    """Compares the two most recent runs. Fewer than two runs means
    there's nothing to compare against yet -- not a regression."""
    if len(history) < 2:
        return None
    current, previous = history[-1], history[-2]
    return evaluate_eval_regression_alert(
        EvalScoreSnapshot(recall=current.recall, precision=current.precision),
        EvalScoreSnapshot(recall=previous.recall, precision=previous.precision),
        max_drop=max_drop,
    )
