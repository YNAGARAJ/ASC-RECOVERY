# MASTER BUILD PROMPT v2 — ASC Recovery Platform

**Supersedes `MASTER-BUILD-PROMPT.md` and `MASTER-PROMPT-AMENDMENTS.md`. Use only this file.**

24 phases. Every gap found across three audits is closed and mapped in Part 3:
a product-completeness review (the six gaps below), and a second, code-level
audit that actually ran against this project's own v1 build through Phase 12
(`docs/audit/`, Waves 0-2 — ten parallel read-only reviewers plus a
consolidation pass, 82 deduplicated findings, full register at
`docs/audit/REGISTER.md`). See "WHAT THE TECHNICAL WIRING AUDIT FOUND" below
for what that second audit adds — it is a different kind of gap than the
product-completeness list, and it changes one claim this file used to make
without qualification.

---

# WHAT THE RE-AUDIT FOUND

The v1 prompt built an accurate engine. It was missing a product. Six gaps were
severe enough to force a rewrite rather than another patch:

| # | Gap | Why severe |
|---|---|---|
| 1 | **No frontend at all** | v1 stopped at the API. Billers work in a UI. This is roughly a third of the remaining build and it was entirely absent. |
| 2 | **No async job infrastructure** | An 835 with 5,000 claims cannot be parsed inside an HTTP request. Every ingestion, reprocess and report needs a worker and a queue. |
| 3 | **Lesser-of contract logic** | Most contracts pay *the lesser of billed charges or fee schedule*. Without it, every claim billed below the fee schedule is reported as underpaid. **This generates false positives on correct payments** — the fastest way to lose a customer's trust. |
| 4 | **Stop-loss / outlier provisions** | High-dollar claims flip to a percentage of billed charges above a threshold (e.g. above $176,893, pay 55% of billed on the entire claim). Without it, every outlier is a false positive. |
| 5 | **Prompt-pay interest** | 49 states mandate interest on late and underpaid claims — Florida is 12% per annum on claims past 20 days. This is recoverable money v1 could not calculate. |
| 6 | **No reprocessing strategy** | When a fee schedule is corrected you must re-run history *without destroying decisions a biller already made*. Retrofitting this is brutal. |

Plus: no clearinghouse or practice-management integration, no compliance
certification path (SOC 2 will be demanded), no performance targets, no
recovery-to-finding attribution, no data lifecycle.

---

# WHAT THE TECHNICAL WIRING AUDIT FOUND

The product-completeness gaps above were found by reading the spec against
the world. This second audit read the *code* — ten parallel, read-only
reviewers run directly against this project's own v1 build through Phase 12,
plus a consolidation pass (`docs/audit/00-inventory.md`,
`00-baseline.md`, `00-conformance.md`, `01-*.md` through `10-*.md`,
`SUMMARY.md`, `REGISTER.md`). It found something this file's opening claim —
"the v1 prompt built an accurate engine" — needed correcting for: one real
CRITICAL money bug, and a structural pattern that explains most of the other
21 HIGH findings at once.

| # | Gap | Why severe |
|---|---|---|
| 1 | **Reversal netting can silently overstate recovered dollars** | A reversal claim with a different line layout than the original drops the offsetting shortfall instead of netting it, or attaches to the wrong service line. This is the exact correctness guarantee "an accurate engine" was supposed to mean. `docs/audit/03-money-correctness.md`, register ID F-01. |
| 2 | **"Built but unwired" is a repeating pattern, not a one-off** | MFA, rate limiting/account lockout, SFTP/S3 ingestion, worklist ranking, most alert evaluators, and — worst — the observability instruments themselves are fully implemented and tested in isolation, and simply never called from the real running system. Six independent instances, found from six different angles by agents with no shared context. `docs/audit/SUMMARY.md`, register IDs F-04–F-06, F-08–F-09, F-11, F-18. |
| 3 | **A gate written as "no bypass exists" is gameable by having no code path at all** | v1's Phase 4 gate said "MFA cannot be bypassed by any code path." True today only because there is no login endpoint of any kind — nothing to bypass. Negative-claim gates prove nothing unless the positive mechanism was independently proven to exist and run. |
| 4 | **Nothing re-verifies an earlier phase's gate after a later phase touches the same code** | Phase 8's business-metrics gap was excused at the time by "no outcome-tracking model exists yet." A later phase built exactly that model, and the excuse went stale silently — nobody caught it until this unplanned audit did. |
| 5 | **No verification floor exists for an environment with no Docker and no live cloud account** | This build never had either. v1's deploy/cloud-portability gates simply went permanently "unverified" with no fallback check, so real deploy-blocking defects — a required secret missing from *both* clouds' infrastructure-as-code, a deploy pipeline that never runs migrations, one cloud's load balancer having no listener at all — shipped undetected through ten phases. |
| 6 | **The app's own startup requirements were never cross-checked against what the infrastructure-as-code actually provisions** | The composition root requires a PHI encryption key at process start; neither cloud's Terraform creates or injects it. The container would crash-loop on the first real deploy — caught only by an audit reading both files side by side, something no phase gate ever required as a step. |

Every finding: file:line, reproduction, and a fix in `docs/audit/01-*.md`
through `10-*.md`. Deduplicated, ranked, with an honest verdict ("not
production grade today," with a realistic effort estimate): `docs/audit/
REGISTER.md`. This file's phase amendments below (marked **Audit
amendment**) close the *pattern*, not just the specific instances already
found — the goal is that the next build of this kind doesn't reproduce gap #2
a seventh time.

---

# READ FIRST

**One phase per session. `/clear` between every one.** Never advance past a failing
gate. No real patient data until Phase 22 is signed.

**Phases 1–8 are foundations — do them in order.** They change the shape everything
else sits on. Later phases have some parallelism, marked where it applies.

---

# PART 0 — SETUP

Unchanged from v1: `CLAUDE.md`, the two guardrail hooks, `/gate` and `/phase`
commands, the `adversarial-reviewer` subagent, `Makefile`. See `PASTE-THIS.md`
prompt #1.

**One addition to `CLAUDE.md`:**

```markdown
9. Access is resolved through membership (user x org x role x facility), never
   through a bare tenant_id. There is no query that skips access resolution.
10. Every money rule is data, not code. Contract terms live in the database and
    are versioned by effective date.
```

---

# PART 0.5 — TWO GATE CRITERIA THAT APPLY TO EVERY PHASE FROM HERE ON

Added by the technical wiring audit. These are not phase-specific — add both
to `/gate`'s checklist once, and run them at the end of *every* phase, not
just the phases that happen to build something security- or deploy-shaped.
They exist because a normal `/gate` run (tests, lint, eval, security scan)
passed clean at every single phase of v1, and the codebase still shipped six
fully-built, fully-tested mechanisms that nothing in production ever called.

**1. The wiring check.** For every new public function or class a phase
adds, `grep` the whole repo for its name before the phase gate can pass. If
it is referenced by nothing except its own test file, the gate is not met —
say so explicitly, don't let "the unit tests pass" stand in for "this is
reachable from a real request." This one check would have caught MFA,
rate limiting, SFTP/S3 ingestion, worklist ranking, and the alert evaluators
at the phase that built them, not six phases later in an unplanned audit.
Concretely: `grep -rn "<new_name>" src/ tests/` — if the only hits are the
definition and its own test, stop and either wire it in or explicitly defer
it in writing (never silently).

**2. The process-boundary smoke test.** Before claiming any deploy- or
composition-root-shaped gate is met, actually start the real production
entrypoint — the literal command the container runs — with placeholder
secrets and no live database, even with no Docker and no cloud account. Hit
the liveness endpoint (must be 200), the readiness endpoint (must correctly
report not-ready against an unreachable database, not crash and not lie),
and one auth-gated endpoint (must correctly reject with no credentials).
This is a five-minute check reachable in *any* environment with just a
Python interpreter, and it is a genuinely different signal than a unit test
of the same code in isolation — the missing-secret and dead-observability
findings above were both things this exact check surfaces immediately once
someone actually runs `main:create_app_from_env` (or your stack's
equivalent) instead of only ever importing it inside a test fixture.

Both checks together are how `docs/audit/07-wiring-integration.md` and
`docs/audit/09-deployment-portability.md` found what ten phases of
individually-green gates missed.

---

# PART 1 — THE PHASES

## FOUNDATIONS

### Phase 1 — Domain core
Pure functions, no I/O. `Money` (Decimal, ROUND_HALF_UP, 2dp — float impossible by
construction) · X12 835 parser (ISA/GS/ST, BPR, TRN, N1, CLP incl. status 22
reversals and 4 denials, NM1, SVC composites with multiple modifiers, CAS triplets
at claim and service level, DTM, LQ, MIA/MOA, PLB) · variance engine with root-cause
classification and evidence strings.

Work test-first: show the tests before the implementation.

**Gate:** mypy strict clean · zero `float` in `domain/` · 100% branch coverage on
variance · parser survives six malformed-input cases.

---

### Phase 2 — Eval harness and golden dataset
Synthetic generator injecting known defects, recording ground truth as **what
actually broke, not what was intended**. ~500-claim frozen golden set. `make eval`
fails below 100% recall or 98% precision.

**Gate:** deliberately breaking one variance rule makes the eval fail. Prove it.

---

### Phase 3 — Persistence foundations
Postgres 16, SQLAlchemy 2.x, Alembic. Claims, service lines, adjustments,
remittances (`file_hash` unique for idempotency), findings with `rule_version`,
append-only `audit_log` with no UPDATE/DELETE grant, `phi_access_log`.

**Do not add `tenant_id`.** Phase 4 supplies the access model.

**Gate:** re-ingesting an identical 835 creates zero new findings.

**Audit amendment (register F-19):** the RLS cross-tenant proof must be
data-driven across *every* table carrying an access-scoping column, not
hand-listed per table. v1 hardcoded a tuple of "the tables RLS applies to"
that didn't track the ORM models — a later phase added a new PHI-bearing
table with no policy and nothing failed. Derive the table list from the
schema itself (every table with the access column, minus an explicit,
reviewed allowlist of genuinely ungated tables like `organizations`/`users`
lookup rows) so a forgotten policy fails the gate automatically instead of
waiting for the next audit to notice.

---

### Phase 4 — Organization and identity model

> Enter plan mode. Build the organization and access model. Every later phase
> depends on this shape, so it comes before security and before any API.
>
> **Schema:**
> - `organizations` — id, parent_org_id (self-referencing, nullable), type
>   (PLATFORM | BILLING_COMPANY | ASC_GROUP | ASC), name, status, settings JSONB
> - `facilities` — id, org_id, name, NPI, tax_id, address, status
> - `users` — id, email, status, mfa_enrolled, last_login_at
> - `memberships` — id, user_id, org_id, role, scope (ALL_FACILITIES | SPECIFIC_FACILITIES)
> - `membership_facilities` — membership_id, facility_id
>
> Every PHI table carries `facility_id`; `org_id` derives through it.
>
> **The one access rule, implemented once:** a user may access a record if they hold
> a membership in the org owning the record's facility *or an ancestor org*, their
> scope includes that facility, and their role grants the permission. Org hierarchy
> is recursive — recursive CTE, indexed, cycle-guarded.
>
> **Roles:** platform_admin, org_admin, manager, biller, analyst, auditor,
> api_service. Permission matrix in `docs/PERMISSIONS.md`.
>
> **Row-Level Security** against resolved facility access.
>
> **Field-level PHI masking:** declarative per-column PHI policy plus a query layer
> that masks PHI columns for roles lacking the permission. `analyst` sees amounts and
> codes, never patient names or member IDs. This is how minimum-necessary is actually
> enforced rather than merely asserted in a policy document.
>
> **Tests that must pass:** a billing-company user scoped to facilities A and B
> cannot read C, blocked at the database with application filtering disabled · a
> parent-org user reads child-org facilities · revoking membership revokes access
> immediately · analyst receives masked PHI · a five-level hierarchy resolves without
> looping.
>
> Plan first. Wait for approval.

**Gate:** all five tests pass, RLS proven as the sole barrier.

---

### Phase 5 — User lifecycle and enterprise access

> Invitation → accept → MFA enrolment → first login, with expiry. **Offboarding that
> kills all active sessions and API keys immediately** — not at next token refresh; a
> departed employee retaining PHI access is a reportable breach. Delegated admin
> (org admins manage only their own org). SSO via OIDC and SAML per organization.
> SCIM 2.0 provisioning and deprovisioning. API keys — scoped, rotatable, expiring,
> never interactive. **Support impersonation** — requires reason, time-boxed, loudly
> audited, visible in the customer's own audit log. Per-org policy: session timeout,
> MFA requirement, IP allowlist. Break-glass with mandatory justification and alerting.
>
> Every action writes to the audit log. Plan first.

**Gate:** offboarding test proves instant session death.

**Audit amendment (register F-04, F-05):** v1 never built this phase at all
— it built MFA's cryptographic mechanism (Phase 6-equivalent) and a session
port, but no phase ever mandated the login endpoint that would call them, and
no table existed to store an MFA secret against a real user. The result:
"MFA cannot be bypassed" was true only because there was nothing to bypass.
Prove the inverse here, positively: run the wiring check (Part 0.5) against
your session-issuance function and confirm its *only* caller outside tests
is this phase's login/accept route. If it has zero production callers after
this phase, the phase is not done, regardless of how well-tested the
mechanism is in isolation.

---

### Phase 6 — Security and PHI controls

> AES-256 at rest including application-level encryption of PHI columns · TLS 1.2+
> enforced · envelope encryption behind a KMS port (AWS KMS / Azure Key Vault / GCP
> KMS / Vault) with KEK rotation that does not re-encrypt all data · **per-org
> encryption keys (BYOK-ready)** · MFA mandatory with no bypass path · short-lived
> tokens, rotating refresh, forced re-auth for PHI export · **PHI redaction at the
> logging source**, with a test that logs a PHI-bearing object and asserts nothing
> sensitive reaches the sink · rate limiting per org · secrets behind an external
> store interface · **data residency configurable per organization**.
>
> Produce `docs/SECURITY.md` mapping each control to its HIPAA citation, plus the
> written asset inventory and network map the 2026 Security Rule requires annually.

**Gate:** PHI redaction test passes · no secrets anywhere in git history.

**Audit amendment (register F-06, F-10):** two specific instances of the
"built but unwired" pattern happened right here in v1 and are cheap to close
now that this is a named, expected failure mode. (1) Rate limiting and
account lockout must be attached as a router- or app-level dependency in
*this* phase, not left as a standalone tested class — apply the wiring check
before moving on. (2) PHI log scrubbing must be installed structurally, once,
on the root logger's handlers via a single logging bootstrap at startup — not
attached one logger at a time. v1 had exactly one `addFilter` call in the
entire codebase; every logger created afterward leaked by default. A test
that logs PHI through a brand-new, never-before-seen logger name and asserts
the sink is clean is the actual proof this control is structural rather than
a discipline someone has to remember.

---

### Phase 7 — Async job infrastructure *(NEW — v1 had none)*

> Enter plan mode. Nothing heavy may run inside an HTTP request.
>
> Job queue and workers (Celery or arq with Redis, or Postgres-backed — must remain
> cloud-portable). Job types: ingestion, variance recomputation, report generation,
> notification dispatch, reprocessing, export.
>
> Required semantics: idempotent jobs · retry with exponential backoff · **dead
> letter queue with alerting** · per-org concurrency limits so one large customer
> cannot starve others · progress reporting for long jobs · cancellation · job
> history with actor and outcome · **jobs carry the access context** so a worker
> cannot read outside its facility scope.
>
> Backpressure: reject or shed load gracefully rather than falling over.
>
> Plan first.

**Gate:** a 5,000-claim 835 processes end to end via a job, with progress visible and
a killed worker resuming without duplicating findings.

---

### Phase 8 — Contract modeling depth *(NEW — the false-positive killer)*

> Enter plan mode. v1's contract model produces false positives on correctly paid
> claims. Fix it properly.
>
> All terms are **data in versioned tables**, effective-dated, never code:
>
> - **Lesser-of logic** — most contracts pay the lesser of billed charges or the fee
>   schedule. Without this, any claim billed below the fee schedule is reported as
>   underpaid when it was paid correctly. **Implement this first.**
> - **Stop-loss / outlier** — above a charge threshold the claim flips to a
>   percentage of billed charges. Support first-dollar language (the percentage
>   applies to the entire claim, not just the excess).
> - **Multiple-procedure reduction** — percentages configurable per payer, not
>   hardcoded at 100/50.
> - Bilateral, assistant surgeon, co-surgeon, discontinued-procedure modifiers.
> - **Implant carve-outs** — support invoice-cost-plus-markup, percent-of-billed, and
>   flat-rate methodologies. Invoice-cost-plus is the most common in real ASC
>   contracts.
> - Case rates, per-diems, percent-of-charge, RVU-based with conversion factors.
> - **Annual escalators** — contracts uplift yearly; misloaded escalators are a
>   leading cause of systematic underpayment.
> - Global periods and NCCI edits.
> - Payer type: Medicare, Medicaid, commercial, workers' comp, auto — different rules.
> - **Prompt-pay interest** — 49 states mandate interest on late or underpaid claims
>   (Florida: 12% per annum past 20 days for electronic claims). Configurable per
>   state, calculated as additional recoverable amount.
> - **Recovery statute of limitations per state** (e.g. North Carolina: 2 years from
>   original adjudication). A finding outside the window is not actionable and must
>   be marked so, not silently listed.
>
> Every rule is unit-tested against a worked example. Extend the golden set to cover
> each new rule, including cases that are **correctly paid under the new rule and
> must not be flagged.**
>
> Plan first.

**Gate:** precision on the extended golden set ≥ 98% · lesser-of and stop-loss cases
produce zero false positives.

**Audit amendment (register F-16, F-17):** v1 shipped two silent pricing
defects here that the domain-layer tests never caught, because the tests
exercised the pricing function directly with hand-built inputs rather than
through the real ingestion path. `BilateralConvention.TWO_LINE_SPLIT` was a
storable enum value with no pricing branch implemented — a contract
configured with it silently priced both lines at full rate, a direct money
error with no error raised anywhere. Implant carve-out logic was correct in
isolation but never fired for real, because the ingestion layer hardcoded
the invoice-cost field to empty regardless of what the source file actually
carried. **Every enum value this phase introduces must have an implementation
before it can be stored on a contract**, and every domain rule's test must
include at least one case that goes through the real parsing/ingestion path
end to end, not only the pure pricing function called directly — that's the
only way a wiring gap between "the field the parser fills in" and "the field
the pricing function reads" gets caught before a real invoice does.

---

## APPLICATION

### Phase 9 — Ingestion pipeline

> Sources behind a port: upload, SFTP poll, S3-compatible drop, clearinghouse API.
> Idempotency by content hash. Quarantine invalid files with a readable diagnostic —
> never silently drop. Partial-batch tolerance. Virus scan. **BPR-to-claims
> reconciliation including PLB** — mismatches usually mean a parser bug. Reversal and
> takeback netting. Full audit per file. Runs as a Phase 7 job.
>
> Also ingest **837 claim files** where available — the 835 alone lacks diagnosis
> codes, units and rendering provider, all of which the appeal packet needs.

**Gate:** same file 3× gives identical totals · injected BPR mismatch is caught.

**Audit amendment (register F-01, the audit's one CRITICAL):** v1's reversal
test only ever exercised a reversal claim with the *same* line layout as the
original. In the real world a reversal frequently reports fewer lines than
what it's reversing — and when it does, v1 either silently dropped the
offsetting shortfall (overstating what the system tells a customer they can
recover) or attached the reversing finding to the wrong service line.
**The reversal-netting test is not complete unless it includes a case where
the reversal claim has fewer lines than the original claim it reverses, and
asserts `sum(shortfall) == 0` afterward.** Match a reversing finding back to
the *original* claim's service line identity, never to the reversal claim's
own line positions — and never let a reversing finding be silently dropped;
an unmatched reversal must fail loudly or quarantine the file, because
silently doing nothing is worse than being wrong noisily.

---

### Phase 10 — API layer

> FastAPI. Every endpoint resolves access through Phase 4 — no endpoint queries
> directly. OpenAPI generated. Pagination everywhere. **No PHI in URLs or query
> strings** (they land in access logs). Structured errors that never echo PHI.
> Request ID propagated into every log line and audit entry. **API versioning
> strategy** with a deprecation policy.
>
> Authorization test matrix: every role × every endpoint × own-facility,
> other-facility-same-org, other-org. Every cell asserted.

**Gate:** full matrix green · no parameter manipulation crosses a facility boundary.

---

### Phase 11 — Finding workflow

> States: new → triaged → assigned → appeal_drafted → approved → submitted →
> payer_acknowledged → recovered | denied | escalated | abandoned | expired. Legal
> transitions enforced in code; every change logged with actor.
>
> Assignment to user or queue, with workload views. Notes and history. Submission
> tracking (method, date, reference, response). **Deadline management** — timely-filing
> clocks per payer with escalating alerts; findings near expiry outrank larger
> findings with time remaining, because expired appeal rights are permanently lost.
> Bulk actions with confirmation and audit. Saved views per user.

**Gate:** illegal state transitions rejected · deadline sort correct across timezones.

**Audit amendment (register F-12):** "every change logged with actor" above
is the right requirement — v1 violated its own equivalent of this rule for
exactly one state transition (recording a recovery outcome, including the
dollar amount, updated the finding with no audit-log entry, while the
adjacent approve/reject transition two functions away did write one). It was
a careful, deliberate build that still missed this, which means "remember to
audit every PHI write" is not a reliable control on its own. **Add a static
check to `/gate`**: every function that writes to a table carrying PHI or a
dollar amount must have a corresponding audit-log call in the same
transaction, checked by grep/AST pattern or a repository-layer wrapper that
makes the audit write structurally unskippable (e.g. a single
`write_with_audit(...)` entry point that every state-mutating function must
go through, rather than each one remembering to call the audit function
separately).

---

### Phase 12 — Frontend application *(NEW — v1 had no UI at all)*

> Enter plan mode. React + TypeScript + Vite. This is where billers actually work.
>
> Screens: login with MFA and SSO · org and facility switcher · dashboard (recoverable
> dollars, ageing, deadlines) · **findings worklist** — filterable, sortable, bulk
> actions, saved views · finding detail with the full evidence chain and line-level
> expected vs allowed · appeal draft review and approval · contract and fee-schedule
> management · **fee schedule import wizard** · user and role administration ·
> reports · audit log viewer for the auditor role.
>
> Requirements: **PHI masking respected in the UI** — an analyst must never receive
> unmasked fields even in a network response · optimistic updates with rollback ·
> proper loading and error states · **WCAG 2.2 AA accessibility** (healthcare buyers
> ask for this in security reviews) · responsive to tablet · session timeout warning ·
> no PHI in browser localStorage, URLs or analytics.
>
> Component tests plus end-to-end tests (Playwright) for the critical paths: login,
> work a finding, approve an appeal, import a fee schedule.
>
> Plan first.

**Gate:** e2e suite green · analyst role receives masked PHI in the network payload,
not merely hidden in the DOM.

---

### Phase 13 — Recovery packet generation

> **Hard boundary: the LLM drafts prose and never computes, adjusts or restates a
> dollar amount.** All figures injected from the finding record. A validator extracts
> every currency figure from the output and asserts an exact match against the
> finding; on mismatch, reject, regenerate, log.
>
> **Also validate citations.** A hallucinated payer policy reference or fabricated
> LCD number in an appeal letter destroys credibility with the payer permanently.
> Every citation must resolve against a known reference table or be stripped.
>
> Minimum-necessary PHI in prompts — substitute placeholders, re-insert after
> generation. Per-payer templates, addresses and submission methods. Prompt
> versioning with its own eval set. Model fallback when the provider is unavailable.
> Per-org LLM cost caps. Human approval before anything is marked ready to send —
> never autonomous.

**Gate:** corrupted draft rejected · fabricated citation stripped · zero patient
identifiers in captured prompts.

**Audit amendment (register F-13, F-14, F-15):** v1's currency validator was
tested against exactly one adversarial case (a deliberately corrupted draft)
and missed two real ways a figure can slip through: a hallucinated
whole-dollar amount written as a bare integer with no `$` and no decimal
point (never matched by the extraction pattern at all), and a *real* figure
from the finding record placed against the *wrong* label (passes a
set-membership check that never verifies which slot a number landed in).
**The validator's test suite must include both shapes explicitly**, not just
"a corrupted draft" generically. Separately: "zero patient identifiers" is
not the same test as "zero PHI." v1 correctly kept patient name and member ID
out of the prompt, but shipped the payer claim control number and date of
service as literal values — both are identifiers under HIPAA, not
"amounts and codes." Placeholder-substitute every claim-level identifier the
same way patient fields already are, and don't ship a real LLM adapter
against real customer data before the BAA covering it exists (Phase 22).

---

### Phase 14 — Integrations *(NEW)*

> Clearinghouse connectors behind one port: **Waystar, Availity, Optum, Trizetto**.
> Transactions: 835 in, 837 out, **276/277 claim status**, 270/271 eligibility.
>
> ASC practice-management systems: **HST Pathways** (dominant for multi-specialty and
> orthopedic ASCs, integrates via Waystar), SIS Complete, Provation, AmkAI, Advantx,
> Nextech ASC, Simplify ASC. HL7 and flat-file adapters.
>
> Accounting export for recovery posting. Webhooks out. Per-integration credential
> storage, health checks, and failure alerting. Every adapter independently testable
> against a recorded fixture.

**Gate:** two clearinghouse adapters pass against recorded fixtures.

---

### Phase 15 — Reporting and notifications

> ROI dashboard per facility and per org: dollars found, recovered, recovery rate,
> time to recovery — by payer, cause, month. Trend analysis. Scheduled reports (PDF,
> CSV, email). Notifications — deadline approaching, high-value finding, recovery
> posted, appeal denied — with per-user preferences and digests. **The compliance
> report:** who accessed which patient's data, when and why.
>
> All reporting respects facility scope and PHI masking. A scheduled report must not
> become a PHI leak by emailing unmasked data to an analyst.

**Gate:** a scheduled report to an analyst contains no unmasked PHI.

---

### Phase 16 — Observability and audit

> OpenTelemetry traces, metrics, structured logs — PHI-scrubbed at source.
> Business metrics: dollars detected and recovered, recovery rate by cause, eval
> scores over time. System metrics: ingestion latency, queue depth, error rate, LLM
> cost per org. Alerts: ingestion failure, eval regression, auth anomaly, unusual PHI
> access volume, cross-facility query attempt, DLQ growth.
>
> **A runbook per alert** — an alert with no runbook wakes someone who cannot act.

**Gate:** exported spans contain no PHI · every alert links to a runbook.

**Audit amendment (register F-08, F-09, F-11):** v1's metrics and tracing
were both correctly built and unit-tested — and both recorded into no-op
providers in the actual running service, because the composition root
constructed the metrics/tracing instruments and then never passed them into
the object that does the real work. **This phase's gate is not met by unit
tests of the metrics/tracing module in isolation.** Run the process-boundary
smoke test (Part 0.5) and confirm a real ingestion or request through the
env-wired app produces a metric/span in a real (even if just in-memory,
for the test) exporter — not just that `Instruments`/`Tracer` objects
construct correctly when tested directly. The same gap applies to alerting:
an evaluator with passing unit tests and zero runtime call site (no
scheduled job, no request hook feeding it real counts) is not "alerting that
exists" — it's a tested pure function. Wire at least a minimal scheduled
check before calling this phase done, even if the paging integration itself
is a later/deferred adapter behind the same port.

---

### Phase 17 — Reprocessing and data lifecycle *(NEW)*

> Enter plan mode. Fee schedules get corrected and rules get fixed. You must re-run
> history **without destroying human work.**
>
> - Recompute findings for a date range, contract or rule version, as a job
> - **Findings a biller has already worked are never silently overwritten** —
>   supersede with a linked new version and flag the change for review
> - Diff report: what changed, why, net dollar impact
> - Rule and fee-schedule version history with rollback
> - Historical backfill for onboarding (the free-analysis pitch needs 1–3 years)
> - Retention enforcement and defensible destruction (6-year HIPAA minimum; some
>   states are longer — make it configurable per org)
> - **Full customer data export in an open format, and certified destruction on
>   termination.** Build this before your first contract, not after your first
>   termination.
> - Archival of cold data to cheaper storage.

**Gate:** reprocessing a corrected fee schedule preserves biller decisions and
produces a correct diff.

---

### Phase 18 — Performance and reliability *(NEW)*

> Define and then prove SLOs: API p95 and p99 latency, ingestion throughput
> (claims/minute), job completion times, availability target.
>
> Load test at 10× expected volume — model a billing company with 20 facilities and
> three years of history. Database index review against real query plans. N+1 query
> detection. Connection pool sizing. Caching with correct invalidation and
> **facility-scoped cache keys** (a cache key missing facility scope is a
> cross-tenant leak). Graceful degradation when the LLM or a clearinghouse is down.
> Chaos testing: kill a worker, kill the database, fill the disk.
>
> Define RTO and RPO, then **prove them with a timed restore drill.**

**Gate:** SLOs documented and met under load · restore drill completed and timed.

---

## DEPLOY AND OPERATE

### Phase 19 — Cloud-agnostic deployment
Multi-stage Dockerfile, non-root, pinned digests. Health, readiness, liveness.
Terraform with a provider-agnostic core and thin AWS/Azure/GCP adapters. **Only
BAA-covered services touch PHI** (AWS 166+, Azure 80+, GCP ~100 — check the current
list). Network segmentation. No public buckets, blocked at account level. Encrypted
backups with a tested restore. Zero-downtime migrations. Devcontainer and
docker-compose for Codespaces (API, Postgres, MinIO, Redis, all health-checked).
`docs/RUNBOOK.md`: deploy, rollback, restore, key rotation, incident response, breach
notification with the 60-day clock.

**Gate:** deploys to two clouds from one Terraform core · fresh Codespace works with
no manual steps.

**Audit amendment (register F-02, F-07, F-20):** three deploy-blocking
defects in v1 were each invisible from either side alone and only showed up
by reading the app and the infrastructure code together — add that
cross-check as an explicit gate step, not an implicit hope. (1) **Every
secret the app's startup validation requires** (grep the composition root's
required-environment-variable list) **must be provisioned and injected by
every cloud module** — diff the two lists as part of the gate; v1 shipped a
required encryption key that neither cloud's secrets module created. (2) A
load balancer or ingress resource is not "done" because the target group and
service exist — **confirm a listener resource actually exists and terminates
traffic on both HTTP-redirect and HTTPS**, for every cloud, not just the one
that happened to work by default. (3) The KMS port needs a real adapter for
each cloud before this phase can claim "cloud-agnostic key management" — a
port definition plus a local/static stopgap adapter is not the same claim,
and v1's docs said as much honestly but the gate still marked the phase done
around that gap rather than naming it as unmet.

---

### Phase 20 — CI/CD and hardening
lint → types → unit → integration → e2e → eval → SAST → dependency scan → container
scan → IaC scan → staging → smoke → manual gate → production. SBOM. Secret scanning
over full history. DAST against staging. Scheduled six-monthly vulnerability scan and
annual penetration test. Then run `adversarial-reviewer` across everything and clear
all HIGH and above.

**Gate:** pipeline green end to end · zero HIGH or CRITICAL open.

**Audit amendment (register F-03, F-23):** v1's deploy pipeline could report
fully green against a freshly-provisioned, still-unmigrated database, because
its smoke test only checked liveness/readiness (which pass against an empty
schema — readiness just needs a reachable connection, not a populated one).
**The deploy smoke test must exercise one real authenticated business
endpoint after migration**, so an unmigrated or mismigrated schema fails the
gate loudly instead of shipping quietly. Separately: "run the adversarial
reviewer once, fix HIGH and above" is a point-in-time check that goes stale
the moment a later phase touches the same code — which is exactly what
happened here (a later phase changed the schema/API surface and nobody
re-ran the review against the new tree until an unplanned audit did, months
of phases later). **Re-run `adversarial-reviewer` — or, better, the full
Wave 0-2 technical audit this file describes — at minimum every 2-3 phases
from here on, not once at the end.** Treat it as a recurring build step with
its own place on the phase timeline, not a final gate you pass once and
forget.

---

### Phase 21 — Compliance certification readiness *(NEW)*

> Your customers will send a security questionnaire before they send a contract.
>
> - **SOC 2 Type II** readiness: control mapping, evidence collection automation,
>   policy set. Start early — Type II requires an observation window of several
>   months, so beginning at sales time means losing the deal.
> - HITRUST assessment if targeting larger health systems
> - State privacy laws beyond HIPAA (CCPA/CPRA for California patients; several
>   states have stricter breach rules than HIPAA)
> - Pre-written security questionnaire answers and a trust page
> - Vendor risk assessments for every subprocessor
> - Annual policy review calendar

**Gate:** a real customer security questionnaire can be answered without engineering.

---

### Phase 22 — Real-data readiness (paperwork, no code)

BAA with cloud provider · BAA with customer · **BAA with your LLM provider, with
zero-retention terms** · security risk analysis · asset inventory and network map ·
incident response plan with named owner · breach notification procedure · workforce
training and sanction policy · retention and destruction schedule · BC/DR plan tested
· subcontractor BAAs · cyber liability insurance · third-party penetration test ·
legal review of the contingency-fee structure.

**Start BAA and insurance conversations during Phase 6.** They run on other people's
calendars and are the long pole.

---

### Phase 23 — First customer pilot

Onboard one ASC. Load their real fee schedule. Ingest one historical quarter. Produce
findings. Have their biller validate them. **Track every outcome — recovered, denied,
abandoned, expired — and feed it back into confidence scoring.**

That loop is the moat. Competitors can copy rules; they cannot copy your record of
which appeals won for which payer. Instrument it from day one.

---

### Phase 24 — Commercial subsystem

> **Recovery attribution:** match a later 835 payment back to the finding that caused
> it. Handle partial recoveries, bundled corrections, and payments spread across
> multiple remittances. Record confidence and evidence for every match — this is the
> basis of your invoice and customers *will* dispute it.
>
> Commercial terms per customer (contingency rate, minimums, caps, exclusions,
> tiers). Invoice generation with line-item traceability to specific findings.
> Dispute workflow with an evidence pack. Usage metering per org.

**Gate:** an invoice traces every dollar to a finding and a matched remittance.

---

# PART 2 — TECHNIQUE PLAYBOOK

| Need | Use |
|---|---|
| Rule that must never break | **Hook** (code enforcement) |
| Always-on context | **CLAUDE.md** (short) |
| Domain knowledge on demand | **Skill** |
| Isolated parallel work | **Subagent** |
| Repeated prompt | **Slash command** |
| External system | **MCP server** |

**Plan mode** to start every phase. **TDD loop**: write failing tests → review them
yourself → implement → `/gate`. **Self-critique** then independent
`adversarial-reviewer` with clean context. **`/clear` between phases, always** —
checkpoint to `docs/PROGRESS.md` at ~60% context, not at 95%.

**Worktrees** for genuinely parallel phases: 14, 15, 16 can run concurrently once
10–12 are done.

---

# PART 3 — GAP REGISTER

Every gap from both audits, and where it is now closed.

**Domain** — lesser-of *(8)* · stop-loss/outlier *(8)* · escalators *(8)* ·
implant carve-out methodologies *(8)* · prompt-pay interest *(8)* · statute of
limitations *(8)* · payer type variation *(8)* · NCCI and global periods *(8)* ·
837 ingestion *(9)* · 276/277 and 270/271 *(14)* · date-of-service contract
pricing *(3, 8)* · reversals *(1, 9)* · unpriced codes surfaced *(1)*

**Access and identity** — org hierarchy *(4)* · facilities *(4)* · membership
scoping *(4)* · seven roles *(4)* · field-level PHI masking *(4)* · RLS on resolved
access *(4)* · invitation and offboarding *(5)* · SSO and SCIM *(5)* · API keys
*(5)* · impersonation *(5)* · break-glass *(5)* · per-org policy *(5)*

**Security** — encryption and MFA *(6)* · per-org keys and BYOK *(6)* · residency
*(6)* · PHI scrubbing *(6)* · append-only audit *(3)* · no PHI in URLs *(10)* ·
cache key scoping *(18)* · secrets in history *(6, 20)*

**Application** — frontend *(12)* · accessibility *(12)* · finding lifecycle *(11)* ·
deadlines *(11)* · assignment *(11)* · bulk actions *(11)* · fee-schedule import
*(12)* · reporting *(15)* · notifications *(15)* · compliance report *(15)*

**Platform** — async jobs and DLQ *(7)* · per-org concurrency *(7)* · reprocessing
without destroying work *(17)* · versioning and rollback *(17)* · retention and
destruction *(17)* · export and offboarding *(17)* · SLOs and load testing *(18)* ·
chaos *(18)* · RTO/RPO drill *(18)* · API versioning *(10)*

**LLM safety** — no model-computed money *(13)* · **citation validation** *(13)* ·
prompt versioning and evals *(13)* · fallback *(13)* · cost caps *(13)* · minimum
PHI in prompts *(13)*

**Commercial** — recovery attribution *(24)* · invoicing *(24)* · disputes *(24)* ·
metering *(24)* · integrations *(14)* · SOC 2 *(21)* · questionnaires *(21)*

**Technical wiring & security** *(found in `docs/audit/`, Waves 0-2 — register
IDs in parentheses)* — reversal netting can overstate recovered dollars,
CRITICAL *(F-01, closed by Phase 9's amendment)* · no authentication path /
MFA unstorable *(F-04, F-05, closed by Phase 5)* · rate limiting and lockout
built but unwired *(F-06, closed by Phase 6)* · PHI log scrubbing not
structural *(F-10, closed by Phase 6)* · RLS coverage hand-maintained, proven
for one table *(F-19, closed by Phase 3)* · bilateral/implant pricing
silently broken on the real ingestion path *(F-16, F-17, closed by Phase 8)*
· currency validator misses bare-integer and wrong-label hallucinations
*(F-14, F-15, closed by Phase 13)* · claim identifiers sent to the LLM
uncounted as PHI *(F-13, closed by Phase 13)* · finding-outcome write with no
audit entry *(F-12, closed by Phase 11)* · observability instruments never
wired from the composition root *(F-08, F-09, closed by Phase 16)* · alert
evaluators with no runtime call site *(F-11, closed by Phase 16)* · SFTP/S3
ingestion sources unreachable in production *(F-18, closed by Phase 9's
existing "sources behind a port" requirement plus the wiring check)* ·
required secret missing from both clouds' infrastructure *(F-02)* · deploy
pipeline never runs migrations, can go green against an empty schema *(F-03)*
· one cloud's load balancer has no listener at all *(F-07)* · no real cloud
KMS adapter for either cloud *(F-20)* · backup/restore never rehearsed or
timed *(F-21, closed by Phase 18)* · gate status not re-verified after a
later phase touches the same code *(F-23, closed by Phase 20's amendment and
Part 0.5)*. The 59-item BACKLOG of MEDIUM/LOW findings (hardcoded config
values, defense-in-depth gaps, deployment drift between clouds, test-fake
fidelity) is not repeated here — see `docs/audit/REGISTER.md`'s own BACKLOG
section, each with a one-line reason it's safe to defer rather than block on.

---

# PART 4 — ORDER

```
FOUNDATIONS (strictly sequential)
  1 domain · 2 evals · 3 persistence · 4 org+identity · 5 users
  6 security · 7 async jobs · 8 contract depth

APPLICATION
  9 ingestion · 10 API · 11 workflow · 12 frontend · 13 packets
  14 integrations · 15 reporting · 16 observability     (14-16 parallelisable)

PLATFORM
  17 reprocessing · 18 performance

DEPLOY
  19 cloud · 20 CI/CD · 21 compliance readiness

LIVE
  22 paperwork (no code) · 23 pilot · 24 commercial
```

**If you have already built v1 Phases 1–12:** you have two separate,
independent tracks, and it matters which you do first.

1. **Fixing the current codebase** — work `docs/audit/REGISTER.md`'s
   MUST-FIX list (23 items, one CRITICAL money bug plus 22 HIGH) directly
   against what exists today, following Wave 3's discipline in
   `docs/AUDIT-PROMPTS.md`: one finding per commit, a failing test before
   the fix, full suite green after. This track does not require adopting
   any of this file's new phases (org/identity model, async jobs, contract
   depth) — it closes real, present-tense defects in the v1 shape.
2. **Adopting this v2 structure** for further build-out — run the audit
   first for a real baseline if you haven't (you have — see above), then do
   Phase 4 (org/identity) *before* any further product work, since it
   changes the schema everything else sits on. Then 5, 7, 8, then the rest.

Do track 1 first if real customer data is anywhere on the near horizon — it's
the shorter, more contained list and it's what actually stands between this
system and a first real customer, per `docs/audit/REGISTER.md`'s own verdict.
Do not build track 2's new phases against a data model (v1's bare `tenant_id`)
you are about to replace with track 2's org/facility/membership model —
that wastes the work twice over.

---

## Sources

- [ASC practice management landscape](https://flexbone.ai/prior-authorization-asc-ehrs/)
- [HST Pathways integrations](https://www.hstpathways.com/products/hst-practice-management-features/)
- [Clearinghouse alternatives](https://intuitionlabs.ai/articles/availity-clearinghouse-alternatives)
- [Claim underpayments in managed care contracts](https://annexmed.com/claim-underpayments-managed-care-contracts)
- [Chargemaster and lesser-of provisions](https://www.ecgmc.com/insights/blog/capture-every-dollar-chargemaster-maintenance-strategies-for-asc-revenue-growth)
- [Orthopedic implant reimbursement trends](https://nimblercm.com/orthopedic-implant-reimbursement-trends-for-ascs/)
- [Interstate comparison of prompt-pay laws](https://capitol.texas.gov/tlodocs/84R/handouts/C3202016033010001/54d4718f-2d41-47bc-9d46-8e797fd8744d.PDF)
- [Florida prompt pay recovery with interest](https://www.frierlevitt.com/results/florida-prompt-pay-law-unpaid-healthcare-claims-interest-recovery/)
- [NC prompt pay requirements](https://www.ncdoi.gov/insurance-industry/form-and-rate-filings/life-and-health/prompt-pay-requirement)
- [HIPAA Security Rule 2026](https://medcurity.com/hipaa-security-rule-changes-2026/)
- [HIPAA-eligible cloud services](https://medcurity.com/hipaa-cloud-compliance/)
