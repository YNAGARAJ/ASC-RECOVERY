# Audit — Wave 1: Test quality (08)

Read-only audit of the test suite's *substance* (not coverage %), plus the
Phase 2 gate's never-demonstrated criterion: proving the eval harness
actually catches a regression. Every command below was really executed in
this session (Windows, Git Bash, Python 3.12, no live Postgres).

---

## SECTION 1 — The eval-regression proof (performed for real)

This is the Phase 2 gate criterion `docs/audit/00-conformance.md` flagged as
never demonstrated: *"prove the eval harness actually catches a regression by
breaking a rule and confirming it fails."* Done here, with a single precise,
described-and-reverted edit.

### The exact change made and reverted
`src/domain/variance.py`, line 134, inside `evaluate_claim`, the per-line
shortfall computation:

```python
# baseline (correct):
shortfall = expected_allowed - actual_allowed
# temporary break (sign flipped):
shortfall = actual_allowed - expected_allowed
```

Flipping the subtraction makes every genuine underpayment produce a *negative*
shortfall. In `evals/run.py::score_cases`, a line only counts as detected when
`actual.shortfall > _TOLERANCE (0.01)` (run.py:146-147), so a negative
shortfall is no longer a detection — every true positive silently becomes a
false negative. The tolerance-band classification uses `abs()`, so the break
does not reclassify lines as `CORRECT_NO_VARIANCE`; it purely destroys recall
and the dollar total, which is exactly the "silent underpayment miss" a real
regression would look like.

### Exact before/after numbers

| Metric | Baseline (clean) | With the one-line break | Gate |
|---|---|---|---|
| golden cases | 504 | 504 | — |
| lines scored | 571 | 571 | — |
| recall | **100.0%** | **0.0%** | 100% |
| precision | 100.0% | 100.0% | ≥ 98% |
| root-cause accuracy | 100.0% | 100.0% | (non-gating) |
| dollar accuracy | 100.0% (158295.80 / 158295.80) | **0.0% (0.00 / 158295.80)** | (non-gating) |
| verdict | **GATE PASSED** | **GATE FAILED** | — |
| process exit code | 0 | **1** | — |

The broken run additionally fired the standing regression detector:
`EVAL REGRESSION ALERT: recall 100.0% -> 0.0%, precision 100.0% -> 100.0%
(threshold: no more than 2% drop in either)`.

### Reversion confirmed
Reverted with `git checkout -- src/domain/variance.py`. `git diff
src/domain/variance.py` is empty, and the re-run returns to **504 cases,
recall 100.0%, precision 100.0%, dollar accuracy 100.0%, GATE PASSED, exit 0**.
The eval-history side effect (`evals/history/runs.jsonl`, gitignored) was
snapshotted before the experiment and restored to its 48-line pre-experiment
state afterward, so this proof left nothing modified on disk.

**Verdict: the Phase 2 gate's third criterion is now genuinely demonstrated.**
The harness catches a broken rule, fails the build with a non-zero exit, and
the golden dataset's frozen expected findings (not recomputed from the changed
code) are what make that failure meaningful.

---

## SECTION 2 — Test-quality findings

### Category verdicts up front
- **Over-mocking / tautological tests: CLEAN.** No `unittest.mock`,
  `MagicMock`, `patch`, or `create_autospec` anywhere in `tests/` (grep,
  zero hits). The suite uses real domain objects, in-memory *adapter* fakes
  (`FakeRepository`, `LocalKMS`, `EicarAwareScanner`), and scripted stubs
  (`ScriptedPacketDrafter`) — not mocks that assert-what-they-were-configured.
  `tests/security/test_redaction.py:49-52` even carries an explicit
  anti-tautology comment ("without this, the test would pass even if the
  filter did nothing"). No test mocks the thing it claims to test; deleting a
  real implementation would fail its test, not pass it.
- **Domain negative/boundary coverage: STRONG.** See the concrete evidence
  under the individual findings below; the domain layer is the best-tested
  part of this repo.
- **Integration across seams: EXISTS BUT ENTIRELY SKIPPED HERE** — see
  [HIGH] finding below.

---

### [HIGH] Every cross-seam integration test is gated behind a live Postgres, so the runnable suite proves no integration behavior
- **File:** `tests/api/test_pilot_workflow_live_db.py`,
  `tests/api/test_endpoints_live_db.py`, `tests/ingestion/test_apply_idempotency.py`,
  `tests/db/*` (28 skipped tests total)
- **What breaks:** The domain→db→api integration tests *do* exist and are
  genuinely end-to-end (`test_pilot_workflow_live_db.py` onboards a tenant,
  ingests a synthetic quarter, pulls findings, records outcomes, and asserts
  confidence scores all the way through the real `PostgresRepository`). But
  every one skips without `TEST_DATABASE_URL`, which is unset in this
  environment and, per `00-baseline.md`, has been unset since before Phase 12.
  The 408 tests that actually run are strictly single-layer or `FakeRepository`-
  backed — **none crosses the real domain→db→api seam.** So a green
  `make test` here is not evidence that ingestion, RLS, effective-dated
  pricing, or the confidence-score join actually work against Postgres; it is
  evidence only about pure logic and the in-memory fake. A repository/schema
  regression (the kind Phase 12 could easily have introduced) would leave
  `make test` green.
- **Reproduce:** `pytest -q` → `408 passed, 28 skipped`; every skip message is
  `TEST_DATABASE_URL is not set`. `grep -rl live_db tests/` lists the
  integration files, all skipped.
- **Fix:** Wire a Postgres 16 service container into the *local* gate (or a
  required CI job) so at least one domain→db→api path and the RLS isolation
  test run on every change, not only in an ad-hoc CI run that predates the
  current commit. Until then, treat "408 passed" as a single-layer result and
  do not read it as integration assurance.
- **Effort:** M

### [MEDIUM] Tenant isolation is proven in the runnable suite only by the fake's Python check, never by real RLS
- **File:** `tests/api/fakes.py:135-139, 220, 276, 297` vs
  `src/api/repository.py:420-425` (PostgresRepository relies on
  `tenant_session` + Postgres RLS)
- **What breaks:** The entire authz matrix (`test_authz_matrix.py`, ~52 cells)
  and every fake-backed API test enforce cross-tenant isolation via an
  in-memory `if tid == tenant_id` dict check. That is a *completely different
  mechanism* from production, where isolation is Postgres RLS inside
  `tenant_session` (`db/repository.py` docstring: "RLS is the actual boundary,
  not app-level filtering"). RLS itself is exercised only by
  `tests/db/test_rls_tenant_isolation.py`, which is skipped here. Consequently,
  if a real `PostgresRepository` method regressed — e.g. ran a query outside
  `tenant_session`, or a new method forgot it — the fake-backed matrix would
  still pass green. The passing authz matrix proves route-level RBAC
  (`can()`) and that no endpoint accepts a client-supplied tenant id
  (`test_tenant_param_absence.py`), which is real and valuable; it does **not**
  prove the production data boundary. This is a PHI cross-tenant-leak risk that
  the loudest-looking test in the repo cannot actually catch in this
  environment.
- **Reproduce:** Read `fakes.py::get_finding_detail` (isolation = a dict
  identity check) against `PostgresRepository` (isolation = RLS). Note
  `test_rls_tenant_isolation.py` skips without `TEST_DATABASE_URL`.
- **Fix:** Keep the fake matrix for RBAC breadth, but make the RLS isolation
  test a required, non-skippable gate (same Postgres-in-CI fix as the HIGH
  finding). Optionally add a mypy/`runtime_checkable` conformance assertion so
  the fake cannot silently drift from the Protocol's *semantics*, not just its
  signatures.
- **Effort:** M

### [MEDIUM] `FakeRepository.generate_packet` can never return `PacketGenerationFailed`, so the 422 currency-rejection route branch has zero runnable coverage
- **File:** `tests/api/fakes.py:212-242` (fake always returns a draft
  `RecoveryPacketSummary` or `None`) vs `src/api/routes/packets.py:38-43`
  (the `isinstance(result, PacketGenerationFailed)` → HTTP 422 branch)
- **What breaks:** The Protocol allows
  `generate_packet -> RecoveryPacketSummary | PacketGenerationFailed | None`,
  and `PostgresRepository` returns `PacketGenerationFailed` when the currency
  validator keeps rejecting the LLM draft (repository.py:705-719). The fake
  fabricates a draft string and never runs the drafter or currency validator
  at all, so it can never produce that value. mypy accepts the fake's narrower
  `... | None` return (covariant return-narrowing), so the omission typechecks
  silently. Result: the API's failure contract — 422 status +
  `PacketGenerationFailedOut` body, the boundary-level safety net for CLAUDE.md
  rule 3 ("no LLM ever restates a dollar amount... validated after") — is
  exercised by **no test at any layer.** The underlying validation logic *is*
  well tested (`tests/packets/test_service.py:73-77` asserts `success is False`
  after `max_attempts`; `tests/packets/test_currency.py:37-46` rejects a
  corrupted draft), but the route's translation of that failure into an HTTP
  response is untested; the live-DB round-trip test uses only a valid draft
  (`test_endpoints_live_db.py:187`).
- **Reproduce:** `grep -rn "422" tests/` → only the pagination `limit` cap
  test; no packet-failure 422 test. `FakeRepository.generate_packet` has no
  code path returning `PacketGenerationFailed`.
- **Fix:** Add a `next_packet_outcome` seam to `FakeRepository` (mirroring its
  existing `next_ingest_outcome`) so a test can force a `PacketGenerationFailed`
  and assert the route returns 422 with the expected body.
- **Effort:** S

### [MEDIUM] `FakeRepository.list_findings` silently ignores date/remittance filters and applies no ordering, diverging from the real repository
- **File:** `tests/api/fakes.py:110-126` vs `src/db/repository.py:659-710`
- **What breaks:** The real `list_findings` filters on
  `ClaimModel.date_of_service` (`date_from`/`date_to`), `remittance_id`, and
  orders by `created_at DESC`. The fake implements only `root_cause`,
  `claim_id`, and `min_shortfall`, with **no ordering** (dict-insertion order).
  Any future API test that asserts date-range filtering, remittance filtering,
  or result ordering would pass against the fake while proving nothing about
  the real query — a passing-fake-test-is-meaningless divergence. Today those
  filter/order code paths are covered only by the skipped live-DB tests, so the
  route→repository filter contract is effectively untested in the runnable
  suite.
- **Reproduce:** Read the two `list_findings` implementations side by side; the
  fake's filter block has no `date_from`/`date_to`/`remittance_id`/`order_by`.
- **Fix:** Bring the fake to parity — apply all six `FindingFilters` fields and
  sort by `created_at` descending — or add a docstring explicitly scoping what
  it does not model, and add live-DB tests that assert the date/ordering
  behavior so the gap is not invisible.
- **Effort:** S

### [MEDIUM] `FakeRepository` compares money as `float`, diverging from Postgres NUMERIC and violating the spirit of CLAUDE.md rule 2
- **File:** `tests/api/fakes.py:123`
  (`float(i.shortfall) >= float(filters.min_shortfall)`)
- **What breaks:** Money must never round-trip through `float` (CLAUDE.md rule
  2). The real filter is `FindingModel.shortfall >= min_shortfall`, an exact
  Decimal/NUMERIC comparison in SQL. The fake coerces both sides to `float`, so
  a `min_shortfall` test at a value that is not exactly representable in binary
  floating point could accept/reject a boundary row differently than Postgres
  would — making a "min_shortfall filter" test that passes against the fake an
  unreliable proxy for real behavior. Low blast radius (comparison only, not
  arithmetic that persists), but it is a real correctness/divergence smell in a
  test double for a money system.
- **Reproduce:** Read `fakes.py:123`; compare to `repository.py:678-679`.
- **Fix:** Compare `Decimal(i.shortfall) >= filters.min_shortfall` (or keep the
  summary's shortfall as `Decimal`), never via `float`.
- **Effort:** S

### [LOW] The fake's `get_claim_access_history` is far simpler than the real join, so a fake-backed access-history assertion cannot vouch for the real report
- **File:** `tests/api/fakes.py:262-270` vs `src/db/repository.py:908-982`
- **What breaks:** The real report resolves a claim's finding ids and packet
  ids, gathers `audit_log` rows across three resource types plus every
  `phi_access_log` row, and merges them chronologically
  (`merge_access_history`). The fake filters one flat `access_events` list by
  `resource_id == str(claim_id)`. `test_authz_matrix.py::
  test_claim_access_history_matrix` only asserts tenant scoping and
  non-emptiness, so this divergence does not currently produce a false pass —
  but the fake cannot stand in for the correctness of the real multi-table
  merge, which is exercised only by the skipped `test_access_history.py` /
  `test_endpoints_live_db.py`. Worth noting so no one later mistakes a
  fake-backed access-history test for evidence of the real reconstruction.
- **Reproduce:** Compare the two implementations.
- **Fix:** None required for the current authz-scoped assertion; ensure the
  real merge stays covered by a non-skippable live-DB test (same Postgres-in-CI
  fix).
- **Effort:** S

### [LOW] `evaluate_claim` has no zero-service-line / empty-claim boundary test
- **File:** `tests/domain/test_variance.py` (19 test functions; none passes an
  empty `priced_claim(())`)
- **What breaks:** Every `test_variance` case has ≥1 line. The empty-claim path
  (`expected.lines == ()` → should return `()` findings) and the single most
  degenerate boundary are untested. Very low risk — the loop simply does not
  execute — but it is the one boundary the otherwise-excellent variance suite
  omits. (The suite *does* cover the tolerance boundary at exactly 0.01,
  0.02-just-beyond, overpayment/negative shortfall, unpriced, duplicate vs
  same-code-different-date, classification precedence, and both branches of the
  stale-fee-schedule optional parameter — genuinely thorough otherwise.)
- **Reproduce:** `grep -n "priced_claim(())" tests/domain/test_variance.py` →
  no hits.
- **Fix:** Add one test: `evaluate_claim("C", priced_claim((), version), ()) ==
  ()`.
- **Effort:** S

---

## Finding count by severity
- CRITICAL: 0
- HIGH: 1 (all integration coverage skipped behind live Postgres)
- MEDIUM: 4 (RLS-only-via-fake · 422 packet-failure branch untested ·
  fake `list_findings` filter/order divergence · fake float money comparison)
- LOW: 2 (fake access-history simplification · missing empty-claim boundary)

## What is genuinely clean (stated with evidence)
- **No over-mocking / no tautological tests** — zero `mock`/`MagicMock`/`patch`
  usage; real objects, in-memory adapters, and scripted stubs throughout;
  explicit anti-tautology guards in the redaction and metrics tests.
- **Domain boundary/negative coverage is strong** — `test_money.py` (round-half-
  up at .005, zero/negative allocate, float/bool/str rejection),
  `test_variance.py` (tolerance band both sides, overpayment, precedence,
  stale-schedule both branches), `test_currency.py` (corrupted-draft rejection,
  no-cents normalization, procedure-code-not-currency).
- **The eval harness genuinely gates** — proven in Section 1 by a real
  break-and-revert: a one-line rule regression drops recall to 0%, fails the
  gate, and returns a non-zero exit.
