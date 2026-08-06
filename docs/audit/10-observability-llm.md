# Audit — Wave 1, Agent 10: Observability & the LLM boundary

Read-only. No application code was modified to produce this file. Every claim
below was verified against the source this session, not copied from Wave 0.

Scope: (1) structured logging + PHI scrubbing at source, (2) trace coverage,
(3) business & system metrics, (4) alerting, (5) audit-log completeness &
append-only enforcement, (6) the LLM/currency boundary.

Headline: the LLM money boundary and the audit-log append-only grant are
**genuinely clean** (evidence below, stated explicitly as required). The
damage is elsewhere and it is the *same* "built, tested in isolation, never
wired into the running system" pattern Wave 0 found in MFA / rate-limiting /
worklist ranking — here it hits **metrics, tracing, and alerting all at once**,
and the single line that breaks metrics and tracing is `main.py:101`.

---

## Category-by-category verdicts

- **Logging / PHI scrubbing at source** — NOT enforced structurally. One
  HIGH finding.
- **Trace coverage** — `PHIScrubbingSpanExporter` is correct but never runs in
  production. One HIGH finding.
- **Metrics** — the ingestion instruments never reach the running pipeline;
  three named business metrics and eval-score/error-rate are missing. One
  HIGH, one MEDIUM.
- **Alerting** — 4 of 5 evaluators have no runtime call site. One HIGH.
- **Audit-log completeness** — one PHI-bearing write path has no audit entry
  (HIGH); two lesser gaps (MEDIUM). Append-only grant is genuinely enforced
  (CLEAN).
- **LLM boundary** — money validation is genuinely enforced and PHI is
  genuinely minimised (CLEAN). Cost is unbounded-by-policy (MEDIUM); a
  words-form figure can evade the digit-only validator (MEDIUM).

---

### [HIGH] Ingestion metrics record to a no-op in production — every business/system ingestion signal is silently dropped
- **File:** src/main.py:101 (root cause); src/api/repository.py:440,476; src/ingestion/pipeline.py:82
- **What breaks:** `create_app_from_env` builds real `instruments` (main.py:90)
  and hands them to the *drafter* (main.py:95) but constructs the repository as
  `PostgresRepository(session_factory, drafter=drafter, encryptor=encryptor)` —
  **no `instruments=` argument.** So `self._instruments` is `None`
  (repository.py:440); `ingest_remittance` forwards that `None` into
  `ingest_file` (repository.py:476); `ingest_file` falls back to
  `noop_instruments()` (pipeline.py:82). Result: in the real running system
  `dollars_detected`, `findings_per_remittance`, `ingestion_latency`, and
  `ingestion_failures` are all recorded into a `NoOpMeterProvider` and go
  nowhere. Every dashboard/alert built on "dollars detected" or "ingestion
  failure rate" reads zero forever while ingestion actually runs. `llm_cost_usd`
  is the *only* metric wired to a real meter (drafter got the instruments).
- **Reproduce:** Read main.py:101 — no `instruments=` / `tracer=` passed to
  `PostgresRepository`. Trace `self._instruments` (None) → repository.py:476 →
  pipeline.py:82 → `noop_instruments()`. `grep -rn "instruments=" src/main.py`
  shows it passed only to `AnthropicPacketDrafter`, never to the repository.
- **Fix:** `PostgresRepository(session_factory, drafter=drafter,
  encryptor=encryptor, instruments=instruments, tracer=setup_tracing(...))` in
  main.py. Add a construction test asserting the repo carries non-None
  instruments/tracer in the env-wired app.
- **Effort:** S

### [HIGH] Trace coverage is zero in production — `PHIScrubbingSpanExporter` is dead code where it matters
- **File:** src/main.py:89; src/observability/tracing.py:90-92; src/ingestion/pipeline.py:81
- **What breaks:** Three compounding gaps mean no span ever reaches the
  scrubbing exporter in the running service. (a) `setup_tracing`
  (tracing.py:90-92) builds a *local* `TracerProvider` and returns its tracer
  but never calls `opentelemetry.trace.set_tracer_provider(...)`, so the global
  OTel provider stays the default no-op. (b) main.py:89 calls
  `setup_tracing(_span_exporter(...))` and **discards the returned tracer.**
  (c) The only span in the entire codebase is
  `resolved_tracer.start_as_current_span("ingestion.ingest_file", ...)`
  (pipeline.py:85); `resolved_tracer` defaults to `NoOpTracer()` when no tracer
  is passed (pipeline.py:81), and per the finding above main.py passes none.
  Net effect: production emits **no traces at all**, and the
  `PHIScrubbingSpanExporter` / `ConsoleSpanExporter`/`OTLPSpanExporter` chain
  wired in main.py is never exercised by a real span. The PHI-scrubbing safety
  wrapper the Phase 8 gate is proud of runs only in its own unit test.
- **Reproduce:** `grep -rn "set_tracer_provider" src/` → no hits.
  main.py:89 discards the return. pipeline.py:81 default is `NoOpTracer()`.
  main.py:101 passes no `tracer=`.
- **Fix:** Have `setup_tracing` register the provider globally (or return it and
  have main pass the resulting tracer into `PostgresRepository(tracer=...)`), and
  pass `tracer=` to the repository at main.py:101. Add a test that ingests via
  the env-wired app and asserts a span reached an in-memory exporter *through*
  `PHIScrubbingSpanExporter`.
- **Effort:** M

### [HIGH] PHI log scrubbing is per-logger opt-in, not enforced at source — a new log statement anywhere leaks by default
- **File:** src/security/redaction.py:22-26,60-79; src/api/errors.py:20 (the only `addFilter` in all of `src/`); src/api/request_context.py:29,52
- **What breaks:** The whole PHI-scrubbing guarantee rests on each logger
  individually having `PHIRedactionFilter` attached. Across the entire codebase
  there is exactly **one** `addFilter` call (errors.py:20). There is no
  `logging.basicConfig`, no `dictConfig`, no root-logger filter, no logging
  bootstrap anywhere (`grep` for `basicConfig|dictConfig|fileConfig` in `src/`
  is empty). The second logger that exists, `api.request` (request_context.py:29),
  logs on every request (line 52) and **has no filter attached** — today it only
  emits `path`/`method`/`request_id` (not PHI), so the live leak is latent, but
  the mechanism is exactly the "depends on every developer remembering" failure
  the assignment asks about. Any future `logging.getLogger("x").info(f"...{mrn}...")`
  in any module bypasses redaction silently, and the redaction module's own
  docstring admits the free-text regex "will not catch an arbitrary patient name
  typed directly into an f-string message." Scrubbing is a discipline, not a
  structural guarantee.
- **Reproduce:** `grep -rn "addFilter" src/` → one hit (errors.py:20).
  `grep -rn "basicConfig\|dictConfig\|fileConfig" src/` → empty.
  request_context.py:29-59 defines a logger and logs on it with no filter.
- **Fix:** Install `PHIRedactionFilter` structurally — attach it in a single
  logging bootstrap (`dictConfig` at app startup) to the root logger *and* to
  the handlers, so every child logger's records pass through it regardless of who
  created the logger. Add a test that logs PHI via a brand-new arbitrary logger
  name and asserts the sink is clean.
- **Effort:** M

### [HIGH] Four of five alert evaluators have no runtime call site — detection logic that never runs against real data
- **File:** src/observability/alerts.py:37-124; only caller: evals/history.py:69
- **What breaks:** `alerts.py` implements five pure evaluators (ingestion
  failure, eval regression, auth anomaly, unusual PHI access, cross-tenant
  probe). Only `evaluate_eval_regression_alert` is ever called, and only from
  `evals/history.py:69` — offline eval tooling run by `make eval`, **not** the
  running service. The other four (`evaluate_ingestion_failure_alert`,
  `evaluate_auth_anomaly_alert`, `evaluate_unusual_phi_access_alert`,
  `evaluate_cross_tenant_probe_alert`) are referenced by nothing but
  `tests/observability/test_alerts.py`. There is no scheduled job, background
  task, or request hook anywhere that feeds real quarantine counts, failed-auth
  counts, PHI-access volumes, or 404-burst counts into these functions. A
  cross-tenant enumeration probe or a biller pulling 500 charts an hour would
  fire nothing — the "alert" exists only as a tested-in-isolation function. Same
  "built but never wired" pattern Wave 0 flagged for MFA/rate-limiting; it
  applies squarely here.
- **Reproduce:** `grep -rn "evaluate_.*alert" src/ evals/ scripts/` → the four
  security/ops evaluators appear only in their own module and test; the running
  `src/` has zero call sites for any of them.
- **Fix:** Add a scheduled evaluator (or wire the counts into request/ingestion
  paths) that periodically queries `audit_log`/`phi_access_log`/remittance
  status, calls each evaluator, and dispatches returned `Alert`s to a
  notification port. At minimum, document these as non-functional until wired so
  no one believes alerting exists.
- **Effort:** L

### [HIGH] Recording a finding outcome writes a PHI-bearing table (including a dollar amount) with no audit-log entry — violates CLAUDE.md rule 5 ("no exceptions")
- **File:** src/api/repository.py:773-795; src/db/repository.py:565-589; route: src/api/routes/findings.py:123-146
- **What breaks:** `POST /findings/{id}/outcome` → `record_finding_outcome`
  updates `findings.outcome`, `findings.amount_recovered`,
  `findings.outcome_recorded_by`, `findings.outcome_recorded_at` on a
  PHI-bearing table. Neither the API-layer method (repository.py:773-795) nor
  the DB-layer function (db/repository.py:565-589) calls `write_audit_log` —
  compare `decide_packet` right below it (repository.py:807) which *does* write
  an audit entry for an analogous state change. Rule 5 is literally "Every write
  to a PHI-bearing table goes through the audit log. No exceptions." Provenance
  is partially captured inline (`outcome_recorded_by/at` columns), but those
  live on the mutable `findings` row, not in the append-only tamper-evident
  `audit_log`, so the one place the system records recovered-dollar outcomes has
  no immutable trail.
- **Reproduce:** Read repository.py:781-795 — no `write_audit_log`. Read
  db/repository.py:565-589 — no `write_audit_log`. Contrast decide_packet at
  repository.py:807.
- **Fix:** Add a `write_audit_log(..., action="finding_outcome_recorded",
  resource_type="finding", resource_id=str(finding_id), phi_accessed=False)`
  inside `record_finding_outcome`'s `tenant_session` block. Add a test asserting
  the audit row appears.
- **Effort:** S

### [MEDIUM] Three named business metrics still missing (dollars_recovered, recovery_rate_by_cause, time_to_recovery) plus eval-scores and API error-rate — and the docstring's excuse is now stale
- **File:** src/observability/metrics.py:1-25 (docstring), 38-46 (`Instruments`)
- **What breaks:** The Phase 8 deliverable named seven business metrics and a
  system-metric set. `Instruments` (metrics.py:38-46) carries only
  `dollars_detected`, `findings_per_remittance`, `ingestion_latency_ms`,
  `ingestion_failures`, `llm_cost_usd`, `queue_depth`. Missing entirely:
  **`dollars_recovered`**, **`recovery_rate_by_cause`**, **`time_to_recovery`**,
  **eval scores** (they live only in `evals/history` + `EvalScoreSnapshot`,
  never emitted as an OTel metric), and a general **API error-rate** counter
  (only quarantine-based `ingestion_failures` exists). The module docstring
  (metrics.py:5-12) still says these are deferred because "no outcome-tracking
  data model exists anywhere in this codebase." **Verified this session that is
  false:** Phase 12 built exactly that model — `src/domain/outcomes.py`, plus
  `findings.outcome` / `findings.amount_recovered` (written by
  `record_finding_outcome`, db/repository.py:565-589, and read by
  `list_historical_outcomes`, db/repository.py:592). The data these three
  metrics need now exists; the metrics and the docstring were never updated.
- **Reproduce:** `grep -n "dollars_recovered\|recovery_rate_by_cause\|time_to_recovery" src/observability/metrics.py`
  → only the docstring, no instrument. `Instruments` has no such fields.
  `src/domain/outcomes.py` and `findings.amount_recovered` exist.
- **Fix:** Add the three recovery instruments (a Counter for `dollars_recovered`,
  a Histogram for `time_to_recovery` in days, and record recovery keyed by
  root_cause for the rate) recorded from `record_finding_outcome`; emit eval
  recall/precision as gauges from the eval run; add an API error-rate counter in
  the exception handler. Delete the stale docstring paragraph.
- **Effort:** M

### [MEDIUM] LLM cost is estimated and logged after the fact, never capped — no per-packet or per-tenant budget enforcement
- **File:** src/packets/drafter.py:35-40,83-95; src/packets/service.py:23,57
- **What breaks:** `estimate_cost` runs *after* `messages.create` returns
  (drafter.py:89) and feeds `record_llm_usage` (drafter.py:94) — a telemetry
  record, not a gate. There is no budget check anywhere: no per-packet dollar
  cap, no per-tenant daily/monthly cap, nothing that aborts before or between
  calls. Per-request cost is only implicitly bounded by `DEFAULT_MAX_TOKENS =
  1024` (drafter.py:23) and `DEFAULT_MAX_ATTEMPTS = 2` (service.py:23). A tenant
  (or a bug) that hammers `POST /findings/{id}/packet` incurs unbounded
  aggregate spend that is only *observed* after the money is gone, and only then
  if metrics were wired (they are for LLM cost — see the one metric that works).
- **Reproduce:** `grep -rn "budget\|cap\|limit" src/packets/` → nothing about
  spend. estimate_cost is called only after the API response (drafter.py:89).
- **Fix:** Introduce a config/metadata-backed per-tenant spend cap checked
  before each draft call (running total from `llm_cost_usd` or a dedicated
  ledger), raising a typed budget-exceeded error the packet route maps to 429/402.
- **Effort:** M

### [MEDIUM] service_line and adjustment inserts have no dedicated audit entry
- **File:** src/ingestion/apply.py:100-123
- **What breaks:** `_apply_claim` writes an audit entry for the parent claim
  (`claim_ingested`, apply.py:88-96) and for each finding (`finding_created`,
  apply.py:150-158), but `create_service_line` (apply.py:100) and
  `create_adjustment` (apply.py:115,126) rows — writes to PHI-bearing tables
  the assignment explicitly names — get **no** audit_log entry of their own. A
  strict reading of rule 5 ("Every write to a PHI-bearing table") is not met;
  reconstruction of "who wrote this service line" relies on the claim linkage
  and the single `claim_ingested` event. Defensible as one-logical-transaction
  auditing, but it is a literal gap against the rule and worth an explicit
  decision.
- **Reproduce:** apply.py:98-134 — no `write_audit_log` between the
  service_line/adjustment inserts.
- **Fix:** Either (a) accept claim-level auditing and document that service_line/
  adjustment writes are covered by the parent `claim_ingested` event, or (b) add
  child-row audit entries. Pick one and record it.
- **Effort:** S

### [MEDIUM] Bulk worklist CSV export writes no audit_log or phi_access_log entry
- **File:** src/api/routes/findings.py:66-108
- **What breaks:** `GET /findings/export.csv` (gated by
  `Action.EXPORT_WORKLIST`) streams up to 10,000 finding rows via
  `list_findings` and writes **no** audit_log or phi_access_log entry. The
  columns are minimum-necessary (no patient name/member id), so it is not a
  direct identifier leak, but a bulk claim-data egress action is exactly what
  HIPAA access logging exists to record, and the single-record `get_finding`
  path *does* log PHI access (repository.py:505). The highest-volume export in
  the system is the one with no trail.
- **Reproduce:** findings.py:66-108 — the export handler calls
  `repository.list_findings` and never touches `write_audit_log` /
  `write_phi_access_log`.
- **Fix:** Emit an audit_log entry (`action="worklist_exported"`, row count in
  metadata) in the export path.
- **Effort:** S

### [MEDIUM] Currency validator only recognises digit-form figures — an LLM restating an amount in words evades both guards
- **File:** src/packets/currency.py:23-26,29-37; src/packets/service.py:57-85
- **What breaks:** `_CURRENCY_PATTERN` matches `$`-prefixed or two-decimal
  numeric strings only. The reject-raw-figure check (service.py:60) and the
  final `validate_currency` check (service.py:74) both rely on this regex. An
  LLM that writes a dollar figure **in words** ("a shortfall of forty-two
  dollars") produces no regex match, so it is neither rejected as a raw figure
  nor caught as an unmatched figure — it passes as "clean prose." CLAUDE.md rule
  3 forbids the LLM restating a dollar amount; a words-form restatement reaches
  the human reviewer unvalidated. The prompt instructs against digits
  (prompt.py:68) which makes this unlikely, but "unlikely" is not the bar rule 3
  sets. Not CRITICAL because the injected/validated numeric figures are still
  correct and present; the risk is an *additional* wrong figure in words beside
  them.
- **Reproduce:** Feed a scripted drafter a response containing "forty-two
  dollars" and no digits; `extract_currency_figures` returns `[]`, so
  `generate_packet_draft` returns `success=True`.
- **Fix:** Add a spelled-out-number detector to the validator, or constrain the
  reviewer UI to surface that money words appeared. Lower-cost mitigation: keep
  the human-approval gate (already present) as the backstop and document the
  limitation.
- **Effort:** M

### [LOW] `queue_depth` is a permanent-zero stub and metric/meter providers are never registered globally
- **File:** src/observability/metrics.py:45,75-83; src/observability/tracing.py:90; src/observability/metrics.py:87
- **What breaks:** `queue_depth` always reports 0 (honestly documented —
  ingestion is synchronous). Separately, `setup_metrics` (metrics.py:87) and
  `setup_tracing` (tracing.py:90) build providers but never call
  `set_meter_provider` / `set_tracer_provider`, so any future code using the
  global OTel API (`metrics.get_meter(...)` / `trace.get_tracer(...)`) silently
  gets no-op instruments with no error — a latent trap that would make a future
  metric "work in tests, do nothing in prod," the same class of bug as the two
  HIGH findings above.
- **Reproduce:** `grep -rn "set_meter_provider\|set_tracer_provider" src/` →
  empty.
- **Fix:** Register both providers globally in `setup_*`, or document that only
  the returned handles are live and all instrumentation must thread through them.
- **Effort:** S

---

## Categories that are genuinely CLEAN (stated explicitly, with evidence)

### LLM money boundary — CLEAN (no model output ever becomes an unvalidated dollar amount)
- **Evidence:** `generate_packet_draft` (service.py:46-99) enforces a two-stage
  guard against the finding's *own* figures. `allowed_amounts` is built solely
  from the finding record's `expected_allowed`/`actual_allowed`/`shortfall`
  (service.py:40-43), the same three values that seed the placeholder
  substitution (prompt.py:52-58). Stage 1 (service.py:60-71): if the raw draft
  contains *any* digit-form currency figure, the attempt is rejected outright —
  regardless of value — so the LLM cannot emit a number even if it happens to be
  correct. Stage 2 (service.py:73-85): after placeholder substitution, every
  currency figure in the final text must be a member of `allowed_amounts` or the
  attempt is rejected. `PacketDraftResult.success` is `True` only on the path
  that passed Stage 2 (service.py:87-92); the failure path returns
  `final_text=None` (service.py:94-99). The finding's real figures are injected
  deterministically by `render_final_text` (prompt.py:81-85), never computed by
  the model. A corrupted draft is genuinely rejected (confirmed by construction;
  the reject-and-regenerate loop is unit-tested per Phase 7). No CRITICAL here.
  (The words-form gap above is a MEDIUM refinement, not a break of the numeric
  guarantee.)

### PHI minimisation in prompts — CLEAN (patient name / member id never enter the prompt text)
- **Evidence:** `build_prompt` (prompt.py:51-78) places `patient_name` and
  `patient_member_id` **only** into the `placeholders` map (prompt.py:52-58),
  never into `BuiltPrompt.text`. The prompt string (prompt.py:59-77) contains
  claim control number, procedure code, date of service, root cause, and
  evidence — no patient identifier. Substitution of `{{PATIENT_REF}}` /
  `{{MEMBER_ID}}` happens *after* generation in `render_final_text`
  (prompt.py:81-85), so the identifiers are never transmitted to the provider,
  not even momentarily. `generate_packet` decrypts the PHI (repository.py:684-689)
  purely to seed the post-generation placeholder map, and writes a
  `phi_access_log` entry first (repository.py:650-656). One caveat, not a
  finding but worth stating: the free-text `evidence` field *is* sent verbatim
  (prompt.py:67); if a future ingestion path ever writes a patient name into a
  finding's `evidence`, it would flow to the LLM — the minimisation guarantee
  holds only as long as `evidence` stays identifier-free.

### Audit log append-only enforcement — CLEAN (real REVOKE, not a comment)
- **Evidence:** `alembic/versions/0001_initial_schema.py:76-78` iterates the
  append-only tables (`audit_log`, `phi_access_log`, defined line 48) and
  executes `GRANT SELECT, INSERT` followed by an explicit
  `REVOKE UPDATE, DELETE ON {table} FROM {app_role}`. This is a real
  migration-time grant against the application role, not a docstring claim. Only
  caveat (infra, not code): enforcement holds only if the app connects as that
  non-owner role and migrations run as a separate owner/superuser — the grant
  itself is genuinely present.

### PHI reads on single-record and packet paths — CLEAN (access logged)
- **Evidence:** `get_finding_detail` writes `write_phi_access_log`
  (repository.py:505-511) before returning decrypted PHI; `generate_packet`
  logs access (repository.py:650-656); `list_packets` logs access
  (repository.py:746-752). The bulk CSV export is the one gap (MEDIUM above).

---

## Summary

- **CRITICAL:** 0
- **HIGH:** 5
- **MEDIUM:** 5
- **LOW:** 1
- **CLEAN (explicit):** LLM money boundary, prompt PHI minimisation, audit_log
  append-only grant, single-record/packet PHI-read logging.

The observability layer is well-*written* but largely not *wired*: metrics and
tracing both die at `main.py:101`, alerting has no runtime driver, and PHI log
scrubbing is opt-in rather than structural. The genuinely reassuring result is
the LLM boundary — the one place a money error would be catastrophic is exactly
the place that is built correctly and defensively.
