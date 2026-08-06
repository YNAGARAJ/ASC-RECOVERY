# Audit — Wave 2: Consolidated Work Register

Deduplicated across all ten Wave 1 files (`01-*.md` through `10-*.md`) plus
`SUMMARY.md`. 97 raw findings collapsed to **82 distinct defects** by merging
15 findings that two or three agents reported independently, from different
angles, as the same underlying defect (kept at the highest severity any
agent assigned it). Every merge is noted explicitly below — nothing was
dropped, only consolidated. Full original write-ups, with reproduction
steps, remain in the source files; this register exists to rank and track,
not to replace them.

**Cross-agent duplicates merged**: no-auth-path/MFA (spec-conformance +
security/PHI + wiring, ×3) · rate-limiting/lockout unwired (spec-conformance +
security/PHI + wiring, ×3) · SFTP/S3 unreachable (spec-conformance + wiring,
×2) · AWS ALB has no listener / no client-API TLS (security/PHI + deployment,
×2) · PHI log scrubbing is per-logger opt-in (security/PHI + observability,
×2) · worklist ranking never applied (spec-conformance + wiring, ×2) · three
business metrics still missing (spec-conformance + observability, ×2) ·
`find_effective_contract` trusts ordering with no overlap guard
(spec-conformance + domain-correctness, ×2) · forced re-auth for PHI export
never invoked (spec-conformance + security/PHI, ×2) · currency validator
misses spelled-out amounts (spec-conformance + observability, ×2) ·
`get_contract_by_payer_id` dead code (spec-conformance + wiring, ×2) ·
`ErrorOut` dead code (spec-conformance + wiring, ×2). One verdict-level
finding ("deployment gate entirely unexecuted," spec-conformance) was folded
into the more specific "backup/restore never rehearsed" row rather than kept
as its own line, since it restated the same fact at a coarser grain.

Severity after merge: **1 CRITICAL · 22 HIGH · 34 MEDIUM · 25 LOW = 82.**

Status column starts `OPEN` for every row — this register is a snapshot from
this audit session, not a live tracker; Wave 3 updates statuses in place as
fixes land.

---

## MUST FIX BEFORE PRODUCTION (all CRITICAL and HIGH — 23 rows)

Ordered by blast radius: money-correctness first, then defects that would
crash or silently misconfigure a real deployment, then standing security
exposure, then correctness gaps, then process/governance.

| ID | Sev | Area | File:line | What breaks | Fix | Effort | Blocks | Status |
|---|---|---|---|---|---|---|---|---|
| F-01 | **CRITICAL** | Money | `ingestion/plan.py:86-100`, `ingestion/apply.py:136-159` | Reversal netting can silently drop the offsetting shortfall (line-count mismatch) or attach to the wrong service line (index reused, procedure differs) — overstates recovered dollars on a real claim. | Match the reversing finding to the *original* claim's service line, not the reversal claim's line positions; never silently drop a reversing finding — fail loudly or quarantine instead. Add a test: `sum(shortfall)==0` after original+reversal with mismatched line counts. | M | Blocks any real pilot (Ph.12) with a real reversal in the file | **FIXED (824b26a)** |
| F-02 | HIGH | Deploy | `main.py:86`, both clouds' `container_runtime.tf`/`secrets_and_kms.tf` | `PHI_ENCRYPTION_KEY` is required at startup; neither cloud's Terraform provisions or injects it. Container crash-loops before serving one request, on both clouds. | Add the secret to both clouds' KMS/Secrets modules and the ECS/Container-App secret injection, same out-of-band pattern already used for `JWT_SECRET_KEY`/`ANTHROPIC_API_KEY`. | S | Blocks Ph.9 deploy entirely | **FIXED (3d18d03)** |
| F-03 | HIGH | Deploy | `.github/workflows/deploy.yml`, `scripts/db/init_roles.sql:14` | No automated `alembic upgrade head` and no `asc_app` role bootstrap in the deploy pipeline — a fresh DB stays unmigrated; the smoke test (`/healthz`, `/readyz`) passes against an empty schema while every real endpoint 500s. | Add an explicit migrate + role-bootstrap job before the service points at the new DB; make the smoke test hit one authenticated read. | M | Blocks Ph.9/10 deploy gate | **FIXED (f51fb31)** |
| F-04 | HIGH | Security | `security/session.py:68`, `api/auth.py`, `api/routes/` (absent) — **merged ×3** (spec-conformance, security/PHI, wiring) | No login/enroll/verify route exists anywhere; `issue_session` (the sole MFA choke point) has zero production callers. "MFA cannot be bypassed" is vacuously true — nothing performs authentication. | Build a login route (OIDC or credential+MFA) that is the sole caller of `issue_session`, sole issuer of production tokens. | L | Blocked by F-05 (storage); blocks real MFA enforcement, Ph.4/6 gate | **FIXED (8d17361)** |
| F-05 | HIGH | Security | `security/mfa.py:17`, `db/models.py:58` (`User`) | The `users` table has no column for an MFA secret — `mfa.py`'s functions cannot be wired to a real user even once F-04's route exists. MFA is unstorable, not just unenforced. | Add an encrypted `mfa_secret` column via `phi_columns`/`EnvelopeEncryptor`. | L | Blocks F-04 | **FIXED (8d17361)** |
| F-06 | HIGH | Security | `api/rate_limit.py:16`, `security/rate_limit.py:79` — **merged ×3** (spec-conformance, security/PHI, wiring) | `enforce_rate_limit` and `AccountLockoutTracker` are complete, tested, and wired to zero routes. Every endpoint — including PHI-decrypting reads — is unthrottled. | Attach `enforce_rate_limit` as a router-level dependency; wire `AccountLockoutTracker` into the login route once F-04 exists. Both are in-memory/single-process — need a shared store before >1 replica. | M | Depends on F-04 for lockout; standalone for rate limiting | **FIXED (5f8d462)** |
| F-07 | HIGH | Deploy/Security | `terraform/modules/aws/container_runtime.tf:193-231`, `db/base.py:13` — **merged ×2** (security/PHI, deployment) | **No `aws_lb_listener` resource exists at all** — the AWS deployment target is unreachable end to end, not just missing TLS; no HTTPS termination, no HSTS, `make_engine` doesn't force `sslmode`. Azure works out of the box; the two "equivalent" clouds are not equivalent. | Add an `aws_lb_listener` on 443 (ACM cert) + a port-80 redirect listener in the environment root (needs a real domain); have `make_engine` require `sslmode=require` defensively. | M | Blocks any real AWS deployment | **FIXED (cf6c3e4, f118ccf)** |
| F-08 | HIGH | Observability | `main.py:101`, `api/repository.py:440,476`, `ingestion/pipeline.py:82` | `create_app_from_env` never passes `instruments=` to `PostgresRepository`; every ingestion metric (`dollars_detected`, `findings_per_remittance`, `ingestion_latency`, `ingestion_failures`) records into a `NoOpMeterProvider` and goes nowhere. | `PostgresRepository(..., instruments=instruments, tracer=tracer)` in `main.py`. Add a construction test asserting the env-wired app carries non-None instruments. | S | Same fix site as F-09 — do together | **FIXED (eafb9ca)** |
| F-09 | HIGH | Observability | `main.py:89`, `observability/tracing.py:90-92`, `ingestion/pipeline.py:81` | `setup_tracing` never calls `set_tracer_provider`; `main.py` discards the returned tracer; the one span in the codebase defaults to `NoOpTracer`. Zero traces reach the deployed system, `PHIScrubbingSpanExporter` is dead code where it matters. | Register the tracer provider globally and pass `tracer=` into `PostgresRepository` at `main.py:101`. Add a test that ingests via the env-wired app and confirms a span reaches an in-memory exporter through the scrubber. | M | Same fix site as F-08 | **FIXED (eafb9ca)** |
| F-10 | HIGH | Security/Observability | `security/redaction.py:22-26`, `api/errors.py:20`, `api/request_context.py:29,52` — **merged ×2** (security/PHI, observability) | Exactly one `addFilter` call in all of `src/`; no logging bootstrap anywhere. PHI scrubbing depends on every future logger remembering to attach the filter — the `api.request` logger already doesn't. | Install `PHIRedactionFilter` structurally via a single `logging.dictConfig` at startup, on the root logger's handlers. Add a test that logs PHI via a brand-new arbitrary logger name and confirms the sink is clean. | M | — | **FIXED (d299b4a)** |
| F-11 | HIGH | Observability | `observability/alerts.py:37-124`, only real caller `evals/history.py:69` | 4 of 5 alert evaluators (ingestion failure, auth anomaly, unusual PHI access, cross-tenant probe) have no runtime call site — only `eval_regression` runs, and only via offline `make eval`. A real cross-tenant probe would fire nothing. | Add a scheduled evaluator (or hook into request/ingestion paths) feeding real counts to each evaluator, dispatching `Alert`s to a notification port. | L | — | **FIXED (37748f7)** |
| F-12 | HIGH | Audit/Compliance | `api/repository.py:773-795`, `db/repository.py:565-589` | `record_finding_outcome` writes `findings.outcome`/`amount_recovered` (a PHI-bearing table, including a dollar amount) with **no `write_audit_log` call** — a direct CLAUDE.md rule 5 violation ("no exceptions"). The analogous `decide_packet` two functions away does audit. This is Phase 12 code from this session. | Add `write_audit_log(..., action="finding_outcome_recorded", ...)` inside the `tenant_session` block. Add a test asserting the audit row appears. | S | — | **FIXED (1f17c10)** |
| F-13 | HIGH | Security/Compliance | `packets/prompt.py:1-12,63-65`, `main.py:95` | `build_prompt` embeds payer claim control number and date of service as literal values and its docstring wrongly calls them "not PHI." The real Anthropic drafter is wired for a running deployment — packet generation transmits re-identifiable claim data to a third party with no BAA. | Placeholder-substitute claim reference and DOS the same way name/amounts already are; correct the docstring; don't enable the real drafter before a BAA exists. | M | Blocks Ph.11/12 real use of the real drafter | **FIXED (f0b163e)** |
| F-14 | HIGH | Money/LLM boundary | `packets/currency.py:23-37`, `packets/service.py:60-85` | Currency validator never matches a bare integer with no `$` and no decimal point — a hallucinated whole-dollar figure written that way bypasses both the raw-draft and post-substitution gates. | Extend detection to bare integers in monetary context, or invert the check to accept only byte-identical injected placeholder strings. Add a negative test. | M | — | **FIXED (b8da7c4)** |
| F-15 | HIGH | Money/LLM boundary | `packets/service.py:40-49,73-85`, `packets/prompt.py:52-85` | Set-membership validation accepts a real figure placed against the wrong label — a token-swap hallucination (real number, wrong slot) passes validation. | Validate position, not just membership: fix slot-to-value mapping deterministically rather than trusting the model's placement, or post-validate each labelled figure against its specific expected value. | M | — | **FIXED (b8da7c4)** |
| F-16 | HIGH | Domain/Money | `domain/contract.py:19-21,182-194` | `BilateralConvention.TWO_LINE_SPLIT` is a storable enum value with **no pricing implementation** — a contract configured with it silently prices both lines at 100% (no bilateral reduction at all), a direct money error. | Implement the two-line-split pricing branch, or reject the value at contract-version creation until implemented. | M | — | **FIXED (43e241b)** |
| F-17 | HIGH | Domain/Money | `ingestion/plan.py:149-159` | Implant carve-out logic is fully correct in `domain/contract.py` but **never fires on the real ingestion path** — `invoice_cost` is always `None`, so every implant line surfaces as `UNPRICED_CODE` with zero shortfall instead of a real recovery amount. Implants are typically the highest-dollar recovery category for an ASC. | Thread the real invoice cost (and revenue code) from the parsed 835/upload metadata through to `ClaimLineInput.invoice_cost` instead of hardcoding `None`. | M | — | **PARTIALLY ADDRESSED (3404f15) — revenue code half fixed as B-16; invoice_cost half stays OPEN, by user decision.** No purchasing-feed integration exists anywhere in this codebase (not a wiring bug — there's nothing to wire); the full write-up (`docs/audit/04-domain-correctness.md`) rates that half at **L effort**, not the M shown here, and explicitly offers "document as out of scope" as the fallback if no such integration is built. Implant lines correctly surface as `UNPRICED_CODE`/`shortfall=0` (never a wrong figure) until a real invoice-cost source exists — deferred to the unbuilt-product-gaps stage of work (see `docs/PROGRESS.md`), not scheduled as a Wave 3 bug fix. |
| F-18 | HIGH | Ingestion | `ingestion/sources.py:33,51,79` — **merged ×2** (spec-conformance, wiring) | `SFTPPollSource`/`S3PollSource`/`UploadSource` are fully built and tested but constructed by nothing except their own test. No poller, scheduler, or route ever pulls from SFTP or S3 — the typical real-world 835 delivery channel for a payer doesn't exist as a running path. | Add a scheduled poller/worker that constructs the poll sources and feeds `ingest_file`, or explicitly stop provisioning S3/IAM resources implying it's supported. | M | — | **FIXED (febdf6e)** |
| F-19 | HIGH | Tenant isolation | `alembic/versions/0001_initial_schema.py:34-45`, `tests/db/test_rls_tenant_isolation.py:45-103` | `_TENANT_SCOPED_TABLES` is a hardcoded tuple that doesn't track `models.py`; the RLS cross-tenant proof exists and is well-designed but covers `claims` only. A future PHI table can ship with no RLS policy and nothing would fail. | Add a data-driven test asserting every table with a `tenant_id` column has RLS enabled+forced+policy, parametrized from `Base.metadata` rather than a hand-maintained list. | M | — | **FIXED (52ba255)** |
| F-20 | HIGH | Deploy/Security | `security/kms_env.py:32-58`, `main.py:96-100`, both clouds' `secrets_and_kms.tf` | No `AwsKmsAdapter`/`AzureKeyVaultAdapter` exists — the app's own PHI-envelope encryption, wherever it runs, uses a static in-secret KEK (`EnvKMS`) that never rotates automatically, with no per-operation KMS audit trail, while both clouds provision a real, rotating KMS key the app never calls. | Implement both cloud adapters behind the existing `KeyManagementService` port; requires a real cloud account to verify. | L | Related to B-20 (KEK rotation on the stopgap adapter) | OPEN |
| F-21 | HIGH | Deploy | `docs/RUNBOOK.md:128-151`, `docs/compliance/README.md:28` | Backup/restore is documented but has never been executed or timed against a real database — the Phase 9 gate ("restore rehearsed and timed") is unmet. *(Absorbs the broader spec-conformance finding "deployment gate entirely unexecuted" — this is its most concrete evidence.)* | Execute the documented restore into a throwaway instance once a database exists; record real wall-clock time. Operational drill, no code change. | M | Blocks Ph.9 gate | OPEN |
| F-22 | HIGH | Test quality | `tests/api/test_pilot_workflow_live_db.py`, `tests/db/*`, `tests/ingestion/*_live_db.py` | Every cross-seam (domain→db→api) integration test is gated behind `TEST_DATABASE_URL`. In every environment without a live Postgres — including this one — the runnable 408-test suite proves no integration behavior at all; a real schema/repository regression would stay green locally. | No code fix — an infrastructure gap. Get a live Postgres into CI (already true) and, ideally, a lightweight local option (e.g. a Docker-based dev-container Postgres) so this isn't Postgres-only in one environment. | — | Same underlying gap as F-01's non-local-verifiability | OPEN |
| F-23 | HIGH | Process | (whole codebase; see F-01 through F-22) | The Phase 10 gate ("adversarial review shows zero HIGH/CRITICAL") does not reflect the current tree — this audit independently found the HIGH findings above in shipped code. Either the adversarial-reviewer subagent was not re-run post-Phase-12, or it was and these weren't surfaced/fixed. | Re-run the `adversarial-reviewer` subagent on HEAD once F-01 through F-22 are triaged; don't treat Phase 10 as passed until it shows clean or every finding is formally accepted. | S | Depends on F-01–F-22 being triaged first | OPEN |

---

## BACKLOG (all MEDIUM and LOW — 59 rows)

Grouped by theme for readability; a one-line reason it's safe to defer
follows each group heading, plus anything row-specific.

### Hardcoded values — config that should be env-driven (deferred: none are wrong today, all are real values that only bite when a second deployment/tenant needs a different one — correctness risk, not a live defect)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-01 | MEDIUM | `api/repository.py:62,658`, `db/models.py:100` | 90-day timely-filing fallback hardcoded, not deployment-configurable |
| B-02 | MEDIUM | `packets/drafter.py:22-32` | LLM model name/token cap/price table hardcoded |
| B-03 | MEDIUM | `security/session.py:37-39` | Token TTLs hardcoded, not policy-driven |
| B-04 | MEDIUM | `api/rate_limit.py:13` | Rate-limit capacity/refill hardcoded at import (moot until F-06 wires it) |
| B-05 | LOW | `observability/alerts.py:39,57,77,92,107` | Alert thresholds hardcoded, no config source |
| B-06 | LOW | `security/rate_limit.py:87-88` | Lockout policy defaults hardcoded (moot until F-06 wires it) |
| B-07 | LOW | `api/repository.py:75`, `db/repository.py:322,669,781` | Default page size (20) hardcoded |
| B-08 | LOW | `observability/metrics.py:86,99` | OTel service name hardcoded |
| B-09 | LOW | `db/base.py:14` | No configurable DB pool sizing |

### Money/domain correctness edges (deferred: none produce a wrong dollar amount on the documented happy path; each is a real edge case worth fixing before it's hit for real)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-10 | MEDIUM | `ingestion/reconcile.py:28-42`, `domain/x835.py:242-260` | BPR reconciliation has no PLB sign normalization — a standard positive-withhold PLB could false-flag or mask a real mismatch |
| B-11 | MEDIUM | `db/repository.py:348-386`, `ingestion/pipeline.py:125-131` | Idempotency protects only byte-identical files; a near-duplicate resend (e.g. re-saved with different whitespace) could double-count |
| B-12 | MEDIUM | `domain/contract.py:112-123` (merged ×2: spec-conformance + domain-correctness) | `find_effective_contract` trusts caller ordering with no overlap guard — safe today only because every caller already sorts correctly |
| B-13 | LOW | `packets/drafter.py:40` | LLM cost estimate uses banker's rounding, not `ROUND_HALF_UP` — telemetry-only, not a billed/paid figure |
| B-14 | LOW | `api/repository.py:675-683`, `domain/variance.py:112-131` | UNPRICED findings can generate a packet asserting a literal zero-dollar figure instead of "no contracted amount on file" |
| B-15 | LOW | `domain/money.py:117` | `Money.allocate` unsound for a negative total (no code path currently passes one) |
| B-16 | MEDIUM | `domain/x835.py:462-482` | SVC04 revenue code is parsed then discarded (hardcoded `None`) — also blocks F-17's implant fix until threaded through. **FIXED (3404f15)** |

### Security/PHI hardening not yet wired (deferred: each is real defense-in-depth, none is an active exploit given no real customer data or traffic exists yet)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-17 | MEDIUM | `security/session.py:127`, `api/routes/findings.py:66` (merged ×2: spec-conformance + security/PHI) | Forced re-auth for PHI export (`require_recent_auth`) is implemented, never invoked by any route |
| B-18 | MEDIUM | `security/rbac.py`, `db/models.py` | "Biller sees only assigned worklists" has no assignment concept in the data model — every biller sees the whole tenant |
| B-19 | MEDIUM | `security/phi_columns.py:3`, `db/models.py:179` | Field-level PHI encryption covers only 2 columns; other claim identifiers sit in plaintext |
| B-20 | MEDIUM | `security/encryption.py:62`, `security/kms_env.py:27,46,53` | KEK rotation is impossible with the only currently-wired adapter (single static key) — superseded once F-20's real cloud adapter lands |
| B-21 | MEDIUM | `api/schemas.py:184-216,139-144`, `api/repository.py:253,255-273` | Unvalidated money/rate strings 500 instead of 422ing at the schema boundary |
| B-22 | LOW | `security/session.py:41-42,135` | JWT secret minimum length documented, not enforced at startup |
| B-23 | LOW | `terraform/modules/azure/database.tf:13` | Azure Postgres doesn't explicitly require secure transport (AWS does) |
| B-24 | LOW | `api/repository.py:744,647` vs `:783,801-802` | Inconsistent explicit-tenant-guard pattern across sibling by-id lookups |

### Tenant isolation defense-in-depth (deferred: RLS is the actual boundary per Ph.3's design; these are belt-and-suspenders, not the belt)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-25 | MEDIUM | `api/repository.py:740-754` | `list_packets` omits the explicit tenant guard its siblings apply before a PHI-access-log write |
| B-26 | MEDIUM | `db/repository.py:713-739` | `get_finding_detail` relies on RLS alone for its joins/`session.get` calls, no explicit `tenant_id` predicate |
| B-27 | LOW | `tests/db/test_rls_tenant_isolation.py:63-81` | RLS control-leg could leave `FORCE ROW LEVEL SECURITY` disabled if the test is interrupted mid-run |

### Wiring gaps beyond the MUST-FIX ones (deferred: each is a real but narrower gap than F-06/F-08/F-09/F-11/F-18)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-28 | MEDIUM | `packets/worklist.py:27`, `api/routes/findings.py:55-63` (merged ×2: spec-conformance + wiring) | Deadline-then-dollar worklist ranking built, never applied — `GET /findings` returns plain `created_at DESC` |
| B-29 | MEDIUM | `api/auth.py:76-79`, `api/repository.py:695,706,729,807,620` | `request_id` never threaded into any audit write; the api-level `write_audit_log` wrapper is dead code |
| B-30 | MEDIUM | `docker-compose.yml:19-24` | `docker compose up` leaves the schema unmigrated — first real request 500s locally too |
| B-31 | MEDIUM | `observability/metrics.py:1-25,38-46` (merged ×2: spec-conformance + observability) | `dollars_recovered`/`recovery_rate_by_cause`/`time_to_recovery` still missing even though Phase 12 removed the excuse |
| B-32 | MEDIUM | `packets/drafter.py:35-40,83-95`, `packets/service.py:23,57` | LLM cost is estimated/logged, never capped per-packet or per-tenant |
| B-33 | MEDIUM | `ingestion/apply.py:100-123` | `service_line`/`adjustment` inserts have no dedicated audit entry (claim-level entry exists) |
| B-34 | MEDIUM | `api/routes/findings.py:66-108` | Bulk worklist CSV export writes no `audit_log`/`phi_access_log` entry |
| B-35 | MEDIUM | `packets/currency.py:23-37` (merged ×2: spec-conformance + observability) | Currency validator only recognizes digit-form figures — an LLM restating an amount in words evades both guards |
| B-36 | LOW | `db/repository.py:225`, `api/repository.py:660-667` (merged ×2: spec-conformance + wiring) | `get_contract_by_payer_id` is dead code — the real packet-deadline path re-implements the same lookup inline |
| B-37 | LOW | `api/schemas.py:332` (merged ×2: spec-conformance + wiring) | `ErrorOut` response schema is unused documentation-as-code |
| B-38 | LOW | `main.py:87` vs every deployment config | `OTEL_EXPORTER_OTLP_ENDPOINT` is read but set nowhere — traces/metrics would go to console even after F-08/F-09 are fixed |
| B-39 | LOW | `.github/workflows/ci.yml:99-127`, `deploy.yml:105-129,198-221` | CI re-implements Makefile targets inline instead of calling `make`; deploy jobs lack their own explicit credential guard |
| B-40 | LOW | `observability/metrics.py:45,75-83,87` | `queue_depth` is a permanent-zero stub; meter/tracer providers never registered globally (root-caused by F-08/F-09) |

### Deployment/portability drift between "equivalent" clouds (deferred: each is real drift, none blocks the AWS-specific F-07 blocker from being fixed first)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-41 | MEDIUM | `container_runtime.tf` (aws vs azure), `docker-compose.yml:31-47` | Health-probe wiring drifts between clouds; compose `app` service has no healthcheck at all |
| B-42 | MEDIUM | `Dockerfile:17,48` | Base image pinned by tag, not digest — reproducibility drifts over time |
| B-43 | MEDIUM | `terraform/modules/azure/secrets_and_kms.tf:75` | Azure `DATABASE_URL` omits `sslmode=require` that AWS includes |
| B-44 | MEDIUM | `terraform/modules/azure/storage.tf:18-24` | 6-year retention lifecycle exists on AWS S3, not on Azure Blob (only a 30-day delete-retention policy) |
| B-45 | MEDIUM | `terraform/README.md:57-73`, `docs/compliance/README.md:19` | BAA-eligibility of every managed service is asserted in comments, never checked against the providers' live published lists |
| B-46 | MEDIUM | `security/rate_limit.py`, both clouds' `variables.tf` (`desired_count`/`min_replicas` default 2) | In-memory rate-limit/lockout state breaks once real replica counts (already 2 by default) are used — relevant once F-06 is wired |
| B-47 | LOW | `Dockerfile:27-46` | `src/` copied before dependency install — busts the dependency layer cache on every code change |
| B-48 | LOW | `docs/RUNBOOK.md:273-277` | "Zero-downtime migrations" section says "0002 through 0004," omits 0005 |
| B-49 | LOW | `Dockerfile:75`, `main.py:87` | Hardcoded port; silent fallback to console exporter with no startup warning in a deployed environment |

### Test quality (deferred: the runnable suite is genuinely strong on pure logic; these are gaps in what it can't currently prove, not bugs in what it does prove)

| ID | Sev | File:line | What breaks (brief) |
|---|---|---|---|
| B-50 | MEDIUM | `tests/api/fakes.py:135-139,220,276,297` | Tenant isolation in the runnable suite is proven only by the fake's Python dict check, never real RLS |
| B-51 | MEDIUM | `tests/api/fakes.py:212-242` | `FakeRepository.generate_packet` can never return `PacketGenerationFailed` — the 422 currency-rejection branch has zero runnable coverage |
| B-52 | MEDIUM | `tests/api/fakes.py:110-126` vs `db/repository.py:659-710` | `FakeRepository.list_findings` ignores date/remittance filters and applies no ordering, diverging from the real repository |
| B-53 | MEDIUM | `tests/api/fakes.py:123` | `FakeRepository` compares money as `float` internally — diverges from real Postgres `NUMERIC`, against the spirit of CLAUDE.md rule 2 (test-only code, not shipped) |
| B-54 | LOW | `tests/api/fakes.py:262-270` vs `db/repository.py:908-982` | Fake's `get_claim_access_history` is simpler than the real join — can't vouch for the real report's correctness |
| B-55 | LOW | `tests/domain/test_variance.py` | No zero-service-line / empty-claim boundary test |
| B-56 | LOW | `domain/x835.py:467-469`, `tests/domain/test_x835.py:106-140` | SVC composite with multiple modifiers works but has no dedicated test |
| B-57 | LOW | `domain/x835.py:60-68,491-498`, `tests/domain/test_x835.py:304-325` | MIA/MOA tests prove presence, not content |
| B-58 | LOW | `domain/contract.py:246` | MPPR tie-breaking is deterministic but untested |
| B-59 | MEDIUM | `alembic/versions/0001_initial_schema.py:50-62` | "Soft-delete with retention policy" is a `deleted_at` column with no actual destruction procedure behind it — a real, separately-tracked open item (`docs/compliance/DATA-RETENTION-SCHEDULE.md`), not new to this audit |

---

## Verdict

**Is this codebase production grade today? No — and not narrowly. This
system should not handle a single real customer record tomorrow.**

Say plainly what's genuinely good, because it's real and it's the harder
half of this system to get right: the money math is float-free and correct
everywhere it was checked, date-of-service contract pricing (the defect the
master prompt itself calls most likely to bite) traces clean end to end, the
LLM can't restate a dollar figure without independent validation at two
separate points, no cross-tenant leak was found by any of the ten agents
despite genuinely trying (including one that could only review test
*design*, not execution, for lack of a live database), and the domain/API
layers are unusually well-tested for pure logic. That is a solid foundation.
It is not a shippable product.

**What actually stands in the way, concretely:**

1. **Nobody can log into this system.** Not "MFA is weak" — there is no
   authentication endpoint of any kind, and the database has nowhere to even
   store an MFA secret if there were. This alone means Phase 4's and Phase
   6's gates, both marked passed in `docs/PHASES.md`, do not reflect what a
   real user could actually do today.
2. **It would not survive its own deploy pipeline.** A required secret
   missing from both clouds' Terraform means the container crash-loops on
   first boot; no automated migration means even a successful boot serves
   500s from an empty schema; the AWS load balancer has no listener at all,
   so the AWS deployment target is unreachable regardless. None of this is
   hypothetical risk analysis — it's what would happen on the next real
   `terraform apply` + deploy, read directly from the HCL and workflow YAML.
3. **The observability this system is proud of doesn't run.** Every
   ingestion metric and every trace goes to a no-op provider because of one
   missing constructor argument in `main.py`. The dashboards Phase 8 was
   built to feed would read zero forever, silently, with no error anywhere
   to indicate why.
4. **The single CRITICAL finding is a real money bug**, not a theoretical
   one: a reversal with a different line count than the original claim can
   silently fail to net out, overstating what the system tells a customer
   they can recover.
5. **Everything else** — rate limiting, worklist ranking, SFTP/S3 ingestion,
   most alerting — follows one repeated pattern: built, well-tested in
   isolation, never connected to the code path that would make it real. That
   pattern alone accounts for roughly a third of the MUST-FIX list.

**Realistic remaining effort**, assuming one engineer working the MUST-FIX
list in the order above, with `make test`/`make lint` green after each
change (Wave 3's own discipline):

- F-01 through F-03, F-08, F-09, F-12: **2-4 days.** Small, mechanical,
  high-confidence fixes (mostly S/M effort, several are one-line wiring
  changes) — no design decisions required.
- F-04, F-05, F-06, F-13 (auth/MFA end to end: storage, route, wiring):
  **1-2 weeks.** The only genuinely new subsystem in this list — a real
  login flow didn't exist before and needs its own design, not just wiring.
- F-14, F-15, F-16, F-17 (money/LLM-boundary hardening, bilateral +
  implant pricing): **3-5 days.** Domain logic changes with real test
  coverage already surrounding them; lower risk than F-04/F-05 but not
  trivial.
- F-07, F-18, F-19, F-20, F-21 (cloud infrastructure — AWS listener, cloud
  KMS adapters, RLS coverage test, a real restore drill): **1-2 weeks**, and
  genuinely blocked on having a real cloud account and a live database in
  this environment, not on engineering time alone.
- F-10, F-11 (structural PHI-scrubbing, alerting wiring): **3-5 days.**
- F-22, F-23 (process): re-running the adversarial reviewer and confirming
  CI is green against the current tree is **under a day** once the above
  land, but must be last, not first.

**Total: roughly 3-4 calendar weeks of focused work**, most of it gated by
access to real infrastructure this build environment doesn't have (Docker, a
live database, a real AWS/Azure account) rather than by code complexity. The
BACKLOG's 59 items are real and worth working through, but none of them are
what stands between this system and a first real customer — the MUST-FIX
list is. And regardless of how fast the MUST-FIX list closes, the separate
15-item compliance checklist this build's own docs already track (BAAs, a
completed penetration test, purchased insurance) is a non-engineering gate
that this audit does not shorten by a single day — see `docs/AUDIT-PROMPTS.md`'s
own closing note.
