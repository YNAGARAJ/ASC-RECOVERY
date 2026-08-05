# AUDIT PROMPTS — ASC Recovery Platform

Full production-readiness audit of Phases 1–12, then remediation.

---

## Read this before you start (90 seconds)

**Six waves, each its own session.** `/clear` between every one.

| Wave | What | Writes code? |
|---|---|---|
| 0 | Set up the audit, inventory the codebase | No |
| 1 | Parallel subagent fan-out — 10 auditors | No |
| 2 | Consolidate into a ranked findings register | No |
| 3 | Remediation loop, severity-ordered | Yes |
| 4 | Codespaces + Docker + cloud portability | Yes |
| 5 | Final verification with proof | No |

**Two rules that make this work:**

1. **Waves 0–2 are strictly read-only.** Nobody fixes anything during the audit. The
   moment you let Claude fix as it finds, it loses the whole picture and you get
   scattered half-fixes that break other things.
2. **All findings go to files, not chat.** Context will fill. `docs/audit/` survives
   `/clear`; a chat message does not.

---

## ⚠️ Read this before Wave 1 — the hardcoded-value trap

You said "no hardcoded values in any module." Taken literally that instruction will
**damage your codebase.** Claude will dutifully move X12 segment identifiers and CARC
code definitions into a YAML file, and you will end up with something slower, harder
to read, and no more configurable.

Three categories, three different homes:

**→ Config file / environment** — things that differ per *deployment*
Database URLs, credentials, bucket names, timeouts, pool sizes, log levels, feature
flags, retention days, rate limits, LLM model names and endpoints, worker counts,
CORS origins.

**→ Metadata table in the database** — things that differ per *tenant, payer or contract*
Fee schedules, contracted rates, MPPR percentages, bilateral multipliers, implant
carve-out terms, percent-of-charge rates, case rates, payer-specific rule overrides,
appeal deadline windows, timely-filing days, finding severity thresholds, dollar
thresholds for worklist prioritisation.

**→ Stays in code, correctly** — things that are fixed by an external standard
X12 segment identifiers (`CLP`, `CAS`, `SVC`), element positions, CARC/RARC code
definitions, claim status code meanings, HTTP status codes, regex for standardised
formats, currency precision of 2, `ROUND_HALF_UP`.

**The test to apply:** *"Would a different customer, payer, or deployment ever need a
different value?"* Yes → config or metadata. No, it's fixed by the X12 standard or by
arithmetic → leave it in code, but named as a constant, never a magic number inline.

This distinction is in the Wave 1 prompt below. Don't remove it.

---

# WAVE 0 — Set up the audit

Paste after `/clear`:

> We have completed Phases 1–12 of `docs/MASTER-BUILD-PROMPT.md`. I want a full
> production-readiness audit before this goes anywhere near real data.
>
> This session is **read-only**. Do not modify a single line of application code.
>
> **Step 1 — Inventory.** Produce `docs/audit/00-inventory.md` containing:
> - Full file tree of `src/`, `tests/`, `evals/`, `terraform/`, `docs/`, with line counts
> - Every module, its public interface, and what imports it
> - A dependency graph showing which modules call which
> - **Orphans:** any module that nothing imports
> - **Dead ends:** any public function never called from anywhere, including tests
> - Every external dependency with its pinned version
>
> **Step 2 — Ground truth.** Actually run these and record real output, not what
> you expect:
> - `make test` — record pass/fail counts and coverage
> - `make lint` — record every violation
> - `make eval` — record recall, precision, cause accuracy
> - `make security` — record findings by severity
> - `docker compose build` and `docker compose up` — record whether it starts
> - Attempt a real request against the running service — record what happens
>
> Write results to `docs/audit/00-baseline.md`. If a command doesn't exist or
> fails, write that down plainly. **Do not fix anything.** A broken baseline is
> the most valuable output of this step.
>
> **Step 3 — Phase conformance map.** Read `docs/MASTER-BUILD-PROMPT.md`. For each
> phase 1–12, produce a table in `docs/audit/00-conformance.md`:
> phase · required deliverables · what actually exists · gate criteria · gate
> genuinely met (yes/no/partial) · evidence.
>
> Be harsh. A file existing is not a deliverable being met.
>
> Then commit and stop.

**Gate:** Three files exist. Baseline records *real* command output. Conformance map
covers all 12 phases.

---

# WAVE 1 — Parallel auditor fan-out

`/clear` first. This is the big one.

> Read `docs/audit/00-inventory.md`, `00-baseline.md` and `00-conformance.md`.
>
> Spawn **ten subagents in parallel**, each with its own clean context. Every one is
> **read-only** — no subagent modifies code. Each writes its findings to its own file
> under `docs/audit/`.
>
> Every finding must use exactly this format:
>
> ```
> ### [SEVERITY] short title
> - **File:** path:line
> - **What breaks:** concrete consequence, in money, data or downtime
> - **Reproduce:** exact steps or command
> - **Fix:** specific change required
> - **Effort:** S / M / L
> ```
>
> Severity: **CRITICAL** (PHI breach, money error, data loss) · **HIGH** (production
> failure, security gap) · **MEDIUM** (correctness risk, maintainability) · **LOW**
> (style, polish).
>
> If a category is clean, state that explicitly. Do not omit it.
>
> ---
>
> **Agent 1 → `docs/audit/01-spec-conformance.md`**
> For every phase 1–12 in the master prompt, verify each deliverable genuinely exists
> and its gate is genuinely met. Flag anything claimed done but stubbed, mocked,
> `TODO`-marked, `pass`-bodied, or asserted without implementation.
>
> **Agent 2 → `docs/audit/02-hardcoded-values.md`**
> Find every literal that should not be a literal. Apply this taxonomy strictly:
> - **Config/env** — differs per deployment: URLs, credentials, buckets, timeouts,
>   pool sizes, log levels, feature flags, retention, rate limits, model names
> - **Metadata table** — differs per tenant/payer/contract: fee schedules, contracted
>   rates, MPPR percentages, bilateral multipliers, carve-out terms, percent-of-charge,
>   case rates, payer rule overrides, appeal deadlines, timely-filing windows, dollar
>   thresholds
> - **Correctly in code** — fixed by external standard: X12 segment IDs and element
>   positions, CARC/RARC definitions, claim status codes, HTTP codes, currency
>   precision 2, ROUND_HALF_UP
>
> Test: *would a different customer, payer or deployment ever need a different value?*
> For each finding state which of the three it belongs in and why. **Do not flag X12
> or CARC constants as violations** — flag them only if they are inline magic values
> rather than named constants.
>
> **Agent 3 → `docs/audit/03-money-correctness.md`**
> Every `float` in a money path. Rounding direction and consistency. Sign errors.
> Double-counting. Ingestion idempotency — does the same 835 twice produce identical
> totals? Reversal/takeback netting. BPR-to-claims reconciliation. Currency precision
> at every boundary including JSON serialisation and the database column type.
>
> **Agent 4 → `docs/audit/04-domain-correctness.md`**
> **Is every claim priced against the contract version effective on its DATE OF
> SERVICE, not today's?** Trace this end to end; it is the most likely silent defect.
> Then: 835 parsing completeness (CLP02 statuses including 22 reversals and 4 denials,
> CAS triplets at both levels, SVC composites with multiple modifiers, PLB, MIA/MOA,
> secondary payer), MPPR ranking logic, bilateral handling, implant carve-out
> calculation, unpriced codes surfaced rather than silently dropped.
>
> **Agent 5 → `docs/audit/05-security-phi.md`**
> PHI in logs, traces, exceptions, URLs, query strings, LLM prompts, test fixtures,
> git history. Encryption at rest and in transit. Key management and rotation. MFA
> enforcement with no bypass path. Session handling. Secrets in the repo or in
> history. Dependency vulnerabilities. Input validation. Authorization on every
> endpoint including IDOR.
>
> **Agent 6 → `docs/audit/06-tenant-isolation.md`**
> Every query, join, subquery, cache key, background job, scheduled task, export and
> log aggregation — is it tenant-scoped? Is Row-Level Security actually enabled and
> enforced, or is the app merely filtering? **Prove it: write a test that disables
> application-level filtering and confirms the database still blocks a cross-tenant
> read.** Report whether that test exists and passes.
>
> **Agent 7 → `docs/audit/07-wiring-integration.md`**
> Is everything actually connected? Orphaned modules. Functions defined and never
> called. API endpoints not reachable. Config keys read but never set, or set but
> never read. Migrations not applied. Event handlers never registered. Dependency
> injection wired but unused. Import cycles. Anything built in one phase that a later
> phase silently bypassed.
>
> **Agent 8 → `docs/audit/08-test-quality.md`**
> Coverage percentage is not the question. Ask: do the tests assert real behaviour or
> tautologies? Over-mocking that would pass even if the implementation were deleted.
> Missing negative and boundary cases. Missing integration tests across module
> seams. Does the eval golden set genuinely catch regressions — **verify by
> temporarily breaking one variance rule in memory and confirming the eval fails**
> (report the result; revert immediately, change nothing on disk).
>
> **Agent 9 → `docs/audit/09-deployment-portability.md`**
> Dockerfile quality: multi-stage, non-root, pinned digests, image size, no secrets
> in layers. Health, readiness, liveness endpoints. Any cloud-specific API, SDK or
> service that would block deployment elsewhere. Terraform provider-agnostic core vs
> per-cloud adapters. Are all PHI-touching services on that provider's BAA list?
> Migration strategy. Backup and restore. Twelve-factor violations.
>
> **Agent 10 → `docs/audit/10-observability-llm.md`**
> Structured logging with PHI scrubbing at source. Trace coverage. Metrics — business
> and system. Alerting. Audit log completeness: is every PHI read and write recorded?
> Is the audit table genuinely append-only with no UPDATE/DELETE grant? Then the LLM
> boundary: **does any model output ever become a dollar amount?** Is the currency
> validator present and does it actually reject a corrupted draft? Is PHI minimised
> in prompts? Are LLM costs bounded?
>
> ---
>
> When all ten have finished, write `docs/audit/SUMMARY.md`: count of findings by
> severity per agent, and the ten single worst findings across the whole codebase.
>
> Commit everything. **Change no application code.**

**Gate:** Ten files plus summary. Every finding has file:line and a reproduction.

---

# WAVE 2 — Consolidate into a work register

`/clear` first.

> Read every file in `docs/audit/`. Produce `docs/audit/REGISTER.md`: a single
> deduplicated, ranked table of every finding.
>
> Columns: `ID · Severity · Area · File:line · What breaks · Fix · Effort · Blocks · Status`
>
> - Merge duplicates found by multiple agents into one row, keeping the highest severity
> - Order by severity, then by blast radius
> - **Blocks:** note where one fix must precede another (config extraction usually
>   precedes almost everything)
> - Status starts as `OPEN`
>
> Then split into two sections:
> - **MUST FIX BEFORE PRODUCTION** — all CRITICAL and HIGH
> - **BACKLOG** — MEDIUM and LOW, with a one-line justification for deferring each
>
> Finally, give me an honest verdict in plain language: is this codebase production
> grade today, and if not, what is the realistic remaining effort? Do not soften it.
>
> Commit and stop.

**Gate:** One register, deduplicated, with a MUST FIX list you can actually work through.

---

# WAVE 3 — Remediation loop

This is the looping part. `/clear` first. **Repeat this prompt until MUST FIX is empty.**

> Read `docs/audit/REGISTER.md`.
>
> Work the **MUST FIX** section in order. For each finding, follow this loop exactly:
>
> 1. State the finding ID and what you are about to change
> 2. **Write a failing test that reproduces the defect** — show it failing
> 3. Make the minimal fix
> 4. Show the test now passing
> 5. Run `make test && make lint` — full suite, not just the new test
> 6. If anything else broke, fix that before continuing
> 7. Mark the finding `FIXED` in `REGISTER.md` with the commit SHA
> 8. `git commit` with the finding ID in the message
> 9. Move to the next finding
>
> Rules:
> - **One finding per commit.** No batching. A batched fix that breaks something is
>   untraceable.
> - Never mark a finding fixed without a test proving it.
> - If a fix turns out larger than its stated effort, stop and tell me rather than
>   improvising a big refactor.
> - If two findings conflict, stop and ask.
>
> Work through as many as you can. When context reaches roughly 60%, update
> `REGISTER.md` and `docs/PROGRESS.md`, commit, and tell me to `/clear` and re-run
> this prompt.
>
> Start with the highest-severity open finding.

**Repeat until:** every CRITICAL and HIGH shows `FIXED`.

---

# WAVE 4 — Codespaces, Docker, cloud portability

`/clear` first.

> Make this run cleanly in GitHub Codespaces and deploy to any cloud.
>
> **1. `.devcontainer/devcontainer.json`**
> - Python 3.12 base image
> - Features: Docker-in-Docker, GitHub CLI, Terraform
> - VS Code extensions: Python, Pylance, Ruff, Docker, PostgreSQL client
> - `forwardPorts` for the API and Postgres, with labels
> - `postCreateCommand` that installs dependencies, runs migrations against the local
>   Postgres, and seeds synthetic data
> - `postStartCommand` that confirms the stack is healthy
> - Non-root `remoteUser`
> - Sensible `hostRequirements`
>
> **2. `docker-compose.yml`** for local and Codespaces: API, Postgres 16 with a named
> volume, an S3-compatible object store (MinIO) so nothing depends on AWS locally.
> Health checks on every service. `depends_on` with `condition: service_healthy`.
>
> **3. `.env.example`** — every configuration key the audit found, documented, with
> safe development defaults and clearly marked required-in-production values. No real
> secrets. Then make the app **fail fast and loudly at startup** if a required
> production variable is missing — a silent default in production is how outages happen.
>
> **4. Cloud portability.** Fix everything Agent 9 raised. Every cloud-specific call
> goes behind a port with adapters. Storage, secrets, KMS and queue must each have an
> interface plus at least a local implementation and one cloud implementation.
>
> **5. Prove it works.** Do not assert — demonstrate, and paste the actual output:
> - `docker compose up` reaches healthy on every service
> - Migrations apply to an empty database
> - `curl` the health endpoint → show the response
> - POST a synthetic 835 through the real API → show the findings returned
> - `make test`, `make lint`, `make eval`, `make security` → show all four results
> - `docker compose down -v && docker compose up` → prove it works from clean
>
> **6. `docs/CODESPACES.md`** — how to open, run, test and debug, plus common problems.
>
> Plan first. Wait for my approval.

**Gate:** A fresh Codespace on a clean clone reaches a working API with no manual steps
beyond opening it.

---

# WAVE 5 — Final verification

`/clear` first.

> Final production-readiness verification. **Read-only. Prove, do not assert.**
>
> 1. Re-run every command from `docs/audit/00-baseline.md`. Produce a before/after
>    table.
> 2. Re-run the `adversarial-reviewer` subagent across the whole codebase with clean
>    context. Report anything it finds that the audit missed.
> 3. Confirm every MUST FIX finding in `REGISTER.md` is `FIXED` and that each has a
>    test proving it.
> 4. Spawn three fresh verification subagents in parallel:
>    - one re-checks PHI leakage across logs, traces, prompts and git history
>    - one re-checks tenant isolation, including the RLS-only test
>    - one re-checks money correctness and date-of-service contract pricing
> 5. Cold-start test: clone to a clean directory, open in Codespaces, follow
>    `docs/CODESPACES.md` exactly as written, and record every step where the docs
>    are wrong or incomplete.
> 6. Write `docs/audit/FINAL-REPORT.md`: what was found, what was fixed, what was
>    deliberately deferred and why, and a clear statement of whether this is
>    production ready.
>
> Then answer one question directly and without hedging: **would you put real patient
> data through this system tomorrow?** If no, list exactly what stands in the way.

**Gate:** Final report written. Honest answer given.

---

## After the audit

Remember Phase 11 of the master prompt is still separate and still mandatory — BAAs,
security risk analysis, incident response plan, penetration test, insurance. A clean
audit means the *code* is ready. It does not make you legally permitted to touch PHI.

Those two things are independent, and only one of them can be fixed by writing code.
