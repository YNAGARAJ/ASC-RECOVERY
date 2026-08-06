# Audit — Wave 1, Agent 1: Spec conformance (Phases 1–12)

Read-only session. Every verdict below was reached by reading the actual
implementation files named, not by confirming a file exists at a path. Where
this agent agrees with `docs/audit/00-conformance.md`, the agreement rests on
independent evidence cited here; where it disagrees (notably Phase 2), that is
called out explicitly.

Severity: CRITICAL (PHI breach, money error, data loss) · HIGH (production
failure, security gap) · MEDIUM (correctness risk, maintainability) · LOW
(style, polish).

---

## Phase 1 — Domain core (pure, no I/O)

**Verdict: MET (clean).**

Evidence read directly:
- `src/domain/money.py` — `Money.__init__` routes through `_reject_non_decimal_source`
  (money.py:14-20), which raises `TypeError` on `float` **and** on `bool`
  (bool-before-int-check ordering is correct, since `bool` is an `int`
  subclass). Construction from `float` is impossible by construction, exactly
  as the prompt requires. Quantization is `ROUND_HALF_UP` to `TWO_PLACES`
  (money.py:68). Arithmetic operators reject non-`Money` operands; `times()`
  requires a `Rate`. No `float` anywhere in the module.
- `src/domain/x835.py` — real recursive-descent-style envelope walk over
  ISA/GS/ST/BPR/TRN/N1/LX/CLP/NM1/DTM/CAS/SVC/LQ/MIA/MOA/PLB/SE (x835.py:365-515).
  CAS parsed as repeating triplets at both claim and service level
  (x835.py:453-461, `_parse_cas` 206-239). `allowed = charge − CO`,
  `paid = allowed − PR` in `_finalize_line` (x835.py:263-286). Reversal
  (CLP02=22) and denial (CLP02=4) are real enum members with parse handling
  (`ClaimStatus`, x835.py:19-24). Malformed inputs are handled without raising:
  missing/short ISA returns a structured `ParseIssue` (x835.py:338-349); a
  per-segment `try/except (InvalidOperation, ValueError, IndexError)`
  (x835.py:516-522) converts a bad segment into a recorded error rather than a
  crash; mixed line endings are stripped per-segment (x835.py:356).
- `src/domain/contract.py` — `price_claim` implements all six rule families in
  an explicit, ordered pipeline: base fee-schedule/percent-of-charge, implant
  carve-out, bilateral-50, assistant surgeon, case-rate allocation, MPPR
  ranking (contract.py:155-267). Effective-dating lookup
  `find_effective_contract` is present and correct (contract.py:112-123).
- `src/domain/variance.py` — root-cause classifier with an `UNPRICED_CODE`
  branch that surfaces (never silently skips) unpriced lines (variance.py:112-131).
  `00-baseline.md` records 100% branch coverage on this file, and the Makefile
  gate (`--fail-under=100` on `variance.py`) enforces it.

The gate ("zero `float` in `domain/`, parser handles the six malformed cases,
100% branch coverage on variance") is genuinely met. `00-baseline.md`'s own
`grep` for `float` in `domain/` came back empty, re-confirmed by reading each
file.

### [LOW] Money.allocate truncates via int() and is unsound for negative totals
- **File:** src/domain/money.py:117
- **What breaks:** `total_cents = int(self._amount * CENTS_PER_UNIT)` truncates
  toward zero. For a negative `Money`, `divmod(total_cents, parts)` then
  distributes remainder cents in a direction that does not preserve
  `sum(result) == self` the way the docstring promises. In practice `allocate`
  is only called on `CaseRateGroup.flat_rate` (a positive case rate,
  contract.py:229), so no live path hits a negative — but the invariant the
  method advertises is not actually universal.
- **Reproduce:** `Money("-1.00").allocate(3)` — inspect that the parts do not
  sum back to `-1.00`.
- **Fix:** quantize/round explicitly instead of `int()`, or assert
  non-negative in `allocate`.
- **Effort:** S

---

## Phase 2 — Eval harness and golden dataset

**Verdict: MET. This agent disagrees with `00-conformance.md`, which marked
Phase 2 PARTIAL for lack of regression-proof evidence.**

The third gate criterion — "deliberately breaking one rule in `variance.py`
makes the eval fail" — **is implemented as an automated test**, which
`00-conformance.md` states does not exist ("no recorded evidence anywhere").
It does exist:

- `tests/evals/test_run.py:276` `test_breaking_a_variance_rule_fails_the_recall_gate`
  asserts `score_cases(GOLDEN_CASES).passed` is True at baseline, then injects
  an evaluator (`_implant_rule_disabled`, test_run.py:254-273) that simulates
  the implant-carve-out rule silently failing by rewriting every
  `IMPLANT_NOT_CARVED_OUT` finding to `CORRECT_NO_VARIANCE`, and asserts
  `broken.recall < 1` and `not broken.passed` (test_run.py:282-283).

This drives the same `evaluator` seam `score_cases` uses in production, so it
is a faithful proxy for "a broken rule fails the gate." It is a slightly
weaker form than physically mutating `variance.py` source (it patches at the
evaluator seam rather than the rule body), which is worth noting, but it does
genuinely demonstrate the harness catches a recall regression — the gate's
intent is met.

`evals/generator.py` (952 lines) injects the full defect catalogue the prompt
names and records ground truth; `evals/golden/cases.py` holds the frozen set
(504 cases at run time per `00-baseline.md`); `evals/run.py` reports recall,
precision, root-cause and dollar accuracy with the gate thresholds encoded.
`make eval` ran clean (100% across the board) per `00-baseline.md`.

No findings.

---

## Phase 3 — Persistence, tenancy, effective-dated contracts

**Verdict: MET in structure; UNVERIFIABLE at runtime here (no live Postgres);
one real MEDIUM gap (retention/destruction).**

Read directly in `alembic/versions/0001_initial_schema.py`:
- RLS is genuinely the enforcement mechanism, not decoration. Every one of the
  10 tenant-scoped tables gets `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
  SECURITY`, and a `tenant_isolation` policy with **both** `USING` and `WITH
  CHECK` keyed on `current_setting('app.tenant_id')::uuid` (0001:80-92). `FORCE`
  is applied deliberately (comment, 0001:82-84) so a future owner-role change
  can't silently disable the policy.
- Append-only is grant-enforced: `audit_log` and `phi_access_log` get
  `GRANT SELECT, INSERT` then `REVOKE UPDATE, DELETE` from the app role
  (0001:76-78). Mutable operational tables get `SELECT, INSERT, UPDATE` and
  **never** `DELETE` (0001:73-74).
- `effective_from`/`effective_to`, `file_hash` UNIQUE-per-tenant, `rule_version`
  on findings, and `phi_access_log` all confirmed present via `db/models.py`
  and the repository read paths.
- Effective-dated pricing logic in `find_effective_contract` (contract.py:112-123)
  correctly filters by payer and the date-of-service window.

The three gate tests exist (`tests/db/test_rls_tenant_isolation.py`,
`test_idempotent_remittance.py`, `test_effective_dated_pricing.py`) and, per
`ci.yml`, run against a real Postgres 16 in CI (0001 migrations run as
`asc_owner`, tests as the non-superuser `asc_app`). This environment has no
live DB, so those three cannot be re-executed here — but the migration and
models are structurally correct on inspection.

### [MEDIUM] "Soft-delete with retention policy" is a column with no destruction path
- **File:** alembic/versions/0001_initial_schema.py:50-62; src/db/models.py (deleted_at)
- **What breaks:** HIPAA requires a *defensible destruction* schedule after the
  6-year retention window, not indefinite retention. `deleted_at` columns exist,
  but the app role is granted no `DELETE` on any table (0001:73-74 grant only
  SELECT/INSERT/UPDATE) and there is no purge/hard-delete routine anywhere in
  `db/repository.py` or `scripts/`. There is literally no code path that can
  ever destroy a PHI-bearing row. Over time this is a compliance liability (data
  that legally must be destroyed cannot be), and `docs/PROGRESS.md` already
  concedes "no hard-delete path for any PHI-bearing row." A column is not a
  retention *policy*.
- **Reproduce:** `grep -rn "DELETE\|delete(" src/db/repository.py` — no
  destruction routine; grants in 0001 never include DELETE on mutable tables.
- **Fix:** add a retention-purge job (privileged role, audited) that hard-deletes
  rows past the retention window, plus a documented schedule.
- **Effort:** M

### [LOW] find_effective_contract silently trusts list order on overlapping versions
- **File:** src/domain/contract.py:112-123
- **What breaks:** the function returns the **first** version whose window
  contains the date of service, in iteration order. If two versions for the same
  payer have overlapping `effective_from`/`effective_to` (a data-entry error),
  pricing silently uses whichever came first in the list rather than flagging the
  ambiguity — a wrong contract could price a claim with no signal.
- **Reproduce:** construct two `ContractVersion`s with overlapping windows for
  one payer; observe no error and order-dependent selection.
- **Fix:** assert non-overlap at contract-version creation, or select the
  most-recent `effective_from` and log when more than one matches.
- **Effort:** S

---

## Phase 4 — Security and PHI controls

**Verdict: PARTIAL. The security core (encryption, redaction, RBAC, secrets)
is real and tested; the authentication/MFA story is the sharpest gap in the
whole build and several controls are built-but-unwired.**

Confirmed real and wired:
- Envelope encryption (`security/encryption.py`, KEK/DEK split) with a KMS port
  (`security/kms.py`) and `LocalKMS`/`EnvKMS` adapters. PHI columns are
  encrypted through it on the ingestion write path (`ingestion/apply.py`,
  `security/phi_columns.py`).
- PHI redaction filter (`security/redaction.py`) + tracing scrubber
  (`observability/tracing.py:32-58`, scrubs span attributes at source). Tested.
- RBAC (`security/rbac.py`) with a full authz matrix (Phase 6).
- Secret store port + `EnvSecretStore` (`security/secrets.py`).

### [HIGH] No authentication path exists — MFA is unenforceable, not just un-bypassable
- **File:** src/security/mfa.py:17-33; src/security/session.py:68-83; src/api/routes/ (no login route)
- **What breaks:** the Phase 4 gate is "MFA cannot be bypassed by any code
  path." Verified by `grep`: `verify_code`, `provisioning_uri`,
  `generate_enrollment_secret` (mfa.py) are referenced **only** by
  `tests/security/test_mfa.py`. `issue_session` is called **only** from test
  conftests and `*_live_db.py` test files — never from any `api/routes/*.py`.
  There is no enrollment route, no verification route, and no login/OIDC
  endpoint anywhere. Consequences: (1) MFA "cannot be bypassed" only vacuously —
  there is no authentication step to bypass; (2) the access token carries no
  `mfa_verified` claim (session.py:139-146), so even a future login endpoint
  minting tokens via `issue_session` produces tokens indistinguishable from any
  other; (3) a real user cannot log into this system by any supported means.
  "MFA mandatory for every user and every session" (a named 2026 HIPAA Security
  Rule requirement in the prompt) is not enforceable because nothing performs
  authentication.
- **Reproduce:** `grep -rn "issue_session\|verify_code" src/` — zero hits under
  `src/api/`. Start the app and look for any auth/login/enroll endpoint in the
  OpenAPI spec: none exists.
- **Fix:** build the OIDC/login + MFA-verification endpoints that call
  `verify_code` then `issue_session`, and add an `mfa` claim validated in
  `validate_access_token`.
- **Effort:** L

### [MEDIUM] Rate limiting and account lockout are built and tested but wired to nothing
- **File:** src/api/rate_limit.py:16 (enforce_rate_limit); src/security/rate_limit.py:79 (AccountLockoutTracker)
- **What breaks:** `enforce_rate_limit` is the only module in `src/` with zero
  importers — no route attaches it via `Depends(enforce_rate_limit)`.
  `AccountLockoutTracker` is referenced only by its own test. So PHI-bearing list
  endpoints (`GET /findings`, CSV export) have no rate limit — a
  compromised/valid token can scrape at unlimited rate — and there is no
  brute-force lockout on failed auth (moot only because there is no auth to
  brute-force). Both controls the prompt requires are non-functional in the
  running system.
- **Reproduce:** `grep -rn "enforce_rate_limit\|AccountLockoutTracker" src/` —
  only definitions, no call sites.
- **Fix:** attach `enforce_rate_limit` as a router-level dependency on the API;
  wire `AccountLockoutTracker` into the (to-be-built) auth path.
- **Effort:** M

### [MEDIUM] Forced re-auth for PHI export (require_recent_auth) is never invoked
- **File:** src/security/session.py:127; src/api/routes/findings.py:66 (export.csv)
- **What breaks:** the prompt requires "forced re-auth for PHI export." The
  step-up primitive `require_recent_auth` exists and is unit-tested, but no
  route calls it — `export_findings_csv` and `approve_packet` proceed on any
  valid, unexpired token regardless of how long ago the user authenticated. The
  most sensitive actions have no step-up.
- **Reproduce:** `grep -rn "require_recent_auth" src/api/` — no hits.
- **Fix:** call `require_recent_auth` in the export and packet-approval
  dependencies; return 401/step-up when stale.
- **Effort:** S

### [MEDIUM] "Biller sees only assigned worklists" — no assignment concept exists
- **File:** src/security/rbac.py; src/db/models.py (no assignment table/column)
- **What breaks:** minimum-necessary is only enforced at tenant granularity. A
  biller sees *every* finding in their tenant, not a subset assigned to them —
  there is no assignment model, column, or filter anywhere. The
  minimum-necessary control the prompt names for the biller role is not
  implemented.
- **Reproduce:** inspect `list_findings` (findings.py:55-63) — filters are
  root-cause/date/dollar/remittance/claim only, never actor-assignment.
- **Fix:** add worklist assignment and scope biller reads to assigned findings.
- **Effort:** M

Note: no real cloud KMS adapter exists (only `LocalKMS`/`EnvKMS`) — a documented,
named deferral rather than a hidden gap; KEK-rotation-without-DEK-re-encryption
is structurally supported by the envelope design.

---

## Phase 5 — Ingestion pipeline

**Verdict: PARTIAL. Core ingestion (idempotency, quarantine, reconciliation,
reversal netting) is real and gated; two of the three required sources are
unreachable in production.**

Confirmed real and wired:
- Idempotency by content hash: `ingest_file` computes `sha256(content)` and
  calls `record_remittance_if_new` (pipeline.py:125-131), backed by the UNIQUE
  `(tenant_id, file_hash)` constraint. Duplicate returns `DuplicateOutcome` with
  no new findings.
- Quarantine with readable diagnostic on virus hit and on UTF-8 decode failure
  (pipeline.py:133-152), each writing an audit entry.
- BPR reconciliation (`ingestion/reconcile.py`) and per-line reconciliation in
  the parser (x835.py:289-317).
- Reversal/takeback netting via `build_ingestion_plan` fed prior findings for
  CLP02=22 control numbers (pipeline.py:165-185).

### [HIGH] SFTP and S3 ingestion sources are unreachable — no production path constructs them
- **File:** src/ingestion/sources.py:51 (SFTPPollSource), :79 (S3PollSource), :33 (UploadSource)
- **What breaks:** the prompt requires three sources "behind a port so a new
  source is a new adapter." Verified by `grep`: `SFTPPollSource`,
  `S3PollSource`, and `UploadSource` are imported only by
  `tests/ingestion/test_sources.py`. `ingest_file` takes raw `content: bytes`
  directly (pipeline.py:61-72); the only real ingress
  (`api/routes/remittances.py`, manual upload) never goes through `UploadSource`.
  There is no poller, scheduled job, or route that pulls from SFTP or S3. Payers
  typically *deliver 835 files by SFTP/S3 drop* — the intended primary ingestion
  channel for a real customer has no working path. Two of three required sources
  do not exist as far as any deployment is concerned.
- **Reproduce:** `grep -rn "SFTPPollSource\|S3PollSource\|UploadSource" src/` —
  no hits outside `sources.py` itself.
- **Fix:** add a scheduled poller (or worker) that constructs the poll sources
  and feeds `ingest_file`; wire it into deployment.
- **Effort:** M

---

## Phase 6 — API layer

**Verdict: MET on the letter of this phase's gate.**

- Tenant scoping is by construction: `tenant_id` is resolved from the token via
  `get_user_by_subject`, never accepted from client input (auth.py:47-79). No
  path/query/body field carries a tenant, so "no endpoint returns another
  tenant's data under parameter manipulation" holds because there is no
  parameter to manipulate.
- Full authz matrix exists (`tests/api/test_authz_matrix.py`), OpenAPI validated
  (`tests/api/test_openapi.py`), pagination on every list
  (`_page`, findings.py:49-52), error redaction tested
  (`tests/api/test_error_redaction.py`), request-id propagated
  (`api/request_context.py`).
- No PHI in URLs/query strings: filters are UUID/enum/date/dollar only
  (findings.py:31-46).

The root MFA/login gap (Phase 4) surfaces here structurally — there is still no
credential endpoint — but that is scored under Phase 4, not double-counted.

### [LOW] ErrorOut response schema is dead code
- **File:** src/api/schemas.py:332 (ErrorOut); src/api/errors.py (_error_body)
- **What breaks:** nothing at runtime — `_error_body` builds a plain
  `dict[str, str]` of the same shape and never references `ErrorOut`. It is
  documentation-as-code that has drifted from the handler it purports to
  describe; a future edit to one won't be caught against the other.
- **Reproduce:** `grep -rn "ErrorOut" src/` — defined, never used.
- **Fix:** type the error handler's return against `ErrorOut`, or delete the model.
- **Effort:** S

---

## Phase 7 — Recovery packet generation

**Verdict: PARTIAL. The money/PHI hard boundary and human-approval step are
genuinely enforced; the worklist-prioritization deliverable is unreachable.**

Confirmed real and wired:
- Hard boundary: the LLM prompt instructs placeholder-only figures
  (prompt.py:51-78); `generate_packet_draft` rejects any raw currency figure in
  the draft *before* substitution and re-validates every figure in the final
  text against the finding record's `{expected, actual, shortfall}` set,
  regenerating up to a cap and recording each rejection (service.py:46-99). The
  API path audits every rejection and the failure (repository.py:694-714).
- PHI minimization: patient name and member id never enter `BuiltPrompt.text`;
  they live only in the post-generation `placeholders` map (prompt.py:51-58).
- Deadlines: `domain/deadlines.py` uses pure `date` arithmetic (timezone-proof,
  leap-year-correct); the packet path computes the deadline from the
  contract's `timely_filing_days` (repository.py:658-669).
- Human approval: `generate_packet` only ever creates `status="draft"`; only
  `approve`/`reject` routes move it (packets.py:24-80).

### [MEDIUM] Worklist ranking (deadline-then-dollar) is built but never applied to any endpoint
- **File:** src/packets/worklist.py:27 (rank_worklist); src/api/routes/findings.py:55-63
- **What breaks:** the prompt explicitly requires worklists "sorted by deadline
  proximity as well as dollar value" — the stated product differentiator.
  `rank_worklist` implements exactly that but is imported only by its own test.
  `GET /findings` returns rows in `created_at DESC` (repository `list_findings`)
  with no deadline/dollar prioritization. A biller cannot see the
  timely-filing-urgent, high-dollar findings first — the exact list that
  prevents permanently-lost appeal windows is unreachable through the API.
- **Reproduce:** `grep -rn "rank_worklist" src/` — only the definition.
- **Fix:** apply `rank_worklist` (or an equivalent ORDER BY on deadline then
  shortfall) in the findings/export read path.
- **Effort:** M

### [LOW] Currency validator only catches digit-form figures, not spelled-out amounts
- **File:** src/packets/currency.py:23-37
- **What breaks:** `_CURRENCY_PATTERN` matches `$`-prefixed or exact-2-decimal
  numbers. An LLM that writes a dollar amount in words ("fifty dollars") would
  not be caught by the raw-figure rejection. Low risk given the prompt strongly
  steers toward tokens, but the "no LLM restates a dollar amount" guarantee has
  a narrow textual gap.
- **Reproduce:** run `validate_currency("fifty dollars", frozenset())` — returns
  valid.
- **Fix:** add a spelled-number heuristic or a stricter template that forbids
  free-text amounts entirely.
- **Effort:** S

### [LOW] get_contract_by_payer_id is dead code (not a wiring bug)
- **File:** src/db/repository.py:225
- **What breaks:** its docstring claims it serves the Phase 7
  `timely_filing_days`/`packet_template` lookup, but the packet path actually
  resolves the contract via `session.get(ContractVersionORM)` →
  `session.get(ContractORM)` (repository.py:660-667). Verified the deadline path
  is correct; this function is simply an unused leftover. Flagged because
  `00-conformance.md` left it "worth a closer look" — resolved: leftover, not a
  gap that changes behavior.
- **Reproduce:** `grep -rn "get_contract_by_payer_id" src/` — only the definition.
- **Fix:** delete it.
- **Effort:** S

---

## Phase 8 — Observability and audit

**Verdict: PARTIAL. The literal gate (no PHI in traces, audit report
reconstructs access history) is met; 3 of 7 named business metrics remain
unbuilt and the reason they were deferred no longer holds.**

Confirmed real:
- `PHIScrubbingSpanExporter` scrubs span attributes at source, wrapping any real
  exporter (tracing.py:61-81); manual-only span creation, no auto-instrumentation.
- Auditor access-history report: `GET /claims/{id}/access-history`,
  tenant-isolated, backed by `phi_access_log` (db/access_history.py, route
  audit.py).
- Real, wired metrics: `dollars_detected`, `findings_per_remittance`,
  `ingestion_latency`, `ingestion_failures`, `llm_cost_per_packet`
  (metrics.py:48-83; recorded from `ingestion.pipeline` and `packets.drafter`).

### [MEDIUM] Three required business metrics are still missing after their blocker was removed
- **File:** src/observability/metrics.py:6-12 (docstring), :38-46 (Instruments)
- **What breaks:** the prompt requires `dollars_recovered`,
  `recovery_rate_by_cause`, and `time_to_recovery`. `metrics.py` still omits all
  three, and its docstring excuses them on the grounds that "no outcome-tracking
  data model exists anywhere in this codebase." That excuse is now stale: Phase
  12 built exactly that model (`domain/outcomes.py`, `findings.outcome` /
  `amount_recovered`, the `POST /findings/{id}/outcome` route). Verified
  `metrics.py` was not updated — `grep` for the three names finds only the
  docstring saying they are not built. The recovery-side business observability
  the product's economics depend on is absent despite the data now existing.
- **Reproduce:** `grep -rn "dollars_recovered\|recovery_rate\|time_to_recovery" src/observability/`
  — only the docstring.
- **Fix:** add the three instruments and record them from the outcome-recording
  path in `api/repository.record_finding_outcome`.
- **Effort:** M

`queue_depth` is a documented always-0 stub (no async queue exists) and the five
alert evaluators (`observability/alerts.py`) are pure detection logic with no
paging integration (deferred to Phase 9/10) — both honestly labeled, not scored
as defects here.

---

## Phase 9 — Cloud-agnostic deployment

**Verdict: NOT MET.**

The Terraform (AWS + Azure modules with a provider-agnostic intent) and a
multi-stage `Dockerfile` are written and reviewable, and `terraform validate`
+ `tfsec` run in CI (`ci.yml` iac-scan job, 164-197). But the gate — "deploys
clean to at least two clouds from the same core · restore-from-backup rehearsed
and timed · no PHI-touching service outside the BAA list" — has never been
executed. This environment has no Docker CLI and no cloud account
(`00-baseline.md`). Nothing has been deployed anywhere; no backup restore has
been rehearsed.

### [HIGH] Deployment gate entirely unexecuted; production readiness unproven
- **File:** terraform/environments/{aws,azure}/main.tf; Dockerfile; .github/workflows/deploy.yml
- **What breaks:** there is no evidence the system deploys or runs on any cloud,
  and the "tested restore" the prompt calls non-negotiable ("an untested backup
  is not a backup") has never been rehearsed. A real customer cannot be
  onboarded (Phase 12) onto infrastructure that has never stood up, and an
  untested restore procedure means data-loss recoverability is unknown.
- **Reproduce:** `docker --version` → not found; no `terraform apply` state; no
  restore drill recorded in `docs/RUNBOOK.md`.
- **Fix:** stand up staging on one cloud, run `terraform apply`, rehearse and
  time a restore, then repeat on a second cloud.
- **Effort:** L

---

## Phase 10 — CI/CD and pre-production hardening

**Verdict: PARTIAL. The per-PR pipeline (lint/test/security/scans) is real and
comprehensive; the deploy→staging→smoke→manual-gate→production→DAST half cannot
run, and the adversarial-review gate is not actually clean.**

`ci.yml` is genuinely thorough: `lint` (ruff, mypy strict, lockfile-drift
check), `test` against a real Postgres 16 service container running every
live-DB test, `security` (bandit, pip-audit, full-history gitleaks), `sbom`,
`container-scan` (trivy, CRITICAL/HIGH fail), `iac-scan` (terraform validate +
tfsec). Guardrail hooks (`scripts/hooks/block_phi.sh`) are real and
non-trivial (SSN/MBI/email/phone/key/secret patterns, `data/real/` path block).

### [HIGH] The Phase 10 adversarial-review gate ("zero HIGH/CRITICAL") is not met on current code
- **File:** (whole codebase); see Phase 4 and Phase 5 HIGH findings above
- **What breaks:** the gate requires "adversarial review shows zero
  HIGH/CRITICAL" before proceeding. This audit independently finds at least two
  standing HIGH issues in shipped code — no authentication/MFA path (Phase 4)
  and unreachable SFTP/S3 ingestion (Phase 5) — plus the unproven deployment
  (Phase 9). Either the adversarial-review subagent was not re-run against the
  post-Phase-12 tree, or it was run and these were not surfaced/fixed. The gate
  as recorded does not reflect the current state.
- **Reproduce:** compare this file's HIGH findings against any recorded
  adversarial-review output; re-run the `adversarial-reviewer` subagent over
  `src/`.
- **Fix:** re-run the adversarial review on HEAD, triage the HIGHs, and resolve
  or formally accept them before treating Phase 10 as passed.
- **Effort:** M

The deploy/staging/smoke/manual-gate/DAST stages live in `deploy.yml` and
require cloud credentials that do not exist, so "pipeline green end to end,
staging fully functional" is unproven — same root cause as Phase 9.

---

## Phase 11 — Real data readiness (compliance gate)

**Verdict: NOT MET (not in dispute).**

`docs/compliance/` contains 7 drafted documents (security risk analysis,
incident-response plan, breach-notification procedure, sanction policy,
retention schedule, BC/DR plan, security-questionnaire answers). None of the
15 checklist items reads DONE with the *external* evidence the gate requires:
no signed cloud-provider BAA, no signed customer BAA, no subcontractor/LLM-provider
BAA with zero-retention terms, no purchased cyber-liability insurance, no
completed third-party pentest, no evidenced workforce training, no rehearsed
tabletop. These are paperwork/process artifacts that cannot be produced from
code, and the repo's own docs say so consistently. Phase 12 was started anyway
by explicit user override of the prompt's sequencing rule — recorded honestly
in `docs/PHASES.md`.

No new code-level findings (this phase is intentionally code-free).

---

## Phase 12 — First customer pilot

**Verdict: NOT MET as literally written (no real customer/data); the
engineering mechanism is real and wired.**

Confirmed real and wired (unlike several earlier "built-but-orphaned"
mechanisms):
- Outcome feedback loop: `domain/outcomes.py` (`Outcome`,
  `validate_outcome_recording`, `calculate_confidence`) is genuinely reachable —
  `POST /findings/{id}/outcome` (findings.py:123-146) calls
  `record_finding_outcome`, which validates via `validate_outcome_recording`
  and persists. `calculate_confidence` returns `None` (not a fabricated number)
  when there is no history (outcomes.py:61-72) — a correct, honest default.
- Outcomes surface in the CSV export (`outcome`, `amount_recovered` columns,
  findings.py:86-102).
- Onboarding mechanism exists (`scripts/onboard_customer.py`,
  `tests/api/test_pilot_workflow_live_db.py`).

The literal gate — "one real customer, one real quarter, findings validated by
their biller, recovery outcomes tracked" — requires a paying customer and real
835 data, which cannot exist before Phase 11 closes (CLAUDE.md rule 1). What
exists is the code a real pilot would need, honestly labeled as synthetic
throughout. The four DB-backed Phase 12 test files have never run against a
live Postgres in this environment (same UNVERIFIABLE-HERE status as Phases 3/5).

No new code-level findings beyond the gate being unmet by definition.

---

## Summary

| Phase | Verdict | One-line reason |
|---|---|---|
| 1 | MET (clean) | float impossible by construction; parser + rules real; variance 100% branch |
| 2 | MET | regression-proof gate IS an automated test (disagrees with 00-conformance) |
| 3 | MET structurally / UNVERIFIABLE at runtime | RLS+FORCE+append-only real; no PHI destruction path |
| 4 | PARTIAL | no auth/MFA path; rate-limit/lockout/step-up all built, unwired |
| 5 | PARTIAL | idempotency/reconcile/netting real; SFTP+S3 sources unreachable |
| 6 | MET | authz matrix, tenant-by-construction, no PHI in URLs |
| 7 | PARTIAL | money/PHI boundary + approval real; worklist ranking unreachable |
| 8 | PARTIAL | trace/audit gates met; 3 business metrics missing, excuse now stale |
| 9 | NOT MET | never deployed to any cloud; restore never rehearsed |
| 10 | PARTIAL | strong per-PR CI; deploy half unrun; adversarial gate not clean |
| 11 | NOT MET | 0/15 items closed with external evidence |
| 12 | NOT MET (literal) | no real customer/data; mechanism real and wired |

**Finding counts:** CRITICAL 0 · HIGH 4 · MEDIUM 6 · LOW 5 (total 15).

- HIGH: no auth/MFA path (P4) · SFTP/S3 unreachable (P5) · deployment gate
  unexecuted (P9) · adversarial-review gate not clean on current code (P10).
- MEDIUM: no PHI destruction path (P3) · rate-limit/lockout unwired (P4) ·
  re-auth-for-export unwired (P4) · no biller worklist assignment (P4) ·
  worklist ranking unreachable (P7) · 3 business metrics missing (P8).
- LOW: Money.allocate negative edge (P1) · effective-contract overlap (P3) ·
  ErrorOut dead code (P6) · currency validator spelled-amount gap (P7) ·
  get_contract_by_payer_id dead code (P7).

**No CRITICAL findings:** the domain money layer is genuinely float-free and
correct; no money error, PHI leak, or data-loss defect was found in shipped
code. The most serious issues are security-control *wiring* gaps (authentication
does not exist) and unexecuted deployment/compliance gates — production-readiness
failures, not active breaches, consistent with a pre-Phase-11 (no real PHI)
system.
