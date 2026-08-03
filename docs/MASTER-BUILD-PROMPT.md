# MASTER BUILD PROMPT
## ASC Underpayment Recovery Platform — gap-free, step by step, with Claude Code

---

# READ THIS FIRST (2 minutes)

**How this works.** This is not one prompt you paste once. It is **12 phases**, each
one a separate Claude Code session. Each phase has: a prompt to paste, a definition
of done, and a **gate** you must pass before moving on.

**The three rules that make or break this:**

1. **One phase per session. `/clear` between phases.** A long session degrades. Fresh
   context per phase is the single biggest quality lever you have.
2. **Never advance past a failing gate.** The gates exist because in healthcare,
   errors compound into either lost money or a breach notification.
3. **No real patient data until Phase 10.** Everything through Phase 9 runs on
   synthetic data. This is not caution, it is law.

**Before you start, understand the one thing that will bite you:** a claim must be
priced against the contract *in force on the date of service* — not today's contract.
Rates change annually, claims arrive months late, and appeals reach back years. If you
build without effective-dated contracts you will rebuild the entire pricing core later.
It is baked into Phase 3 below.

---

# PART 0 — SETUP (do this once, 30 minutes)

## 0.1 Create the repo

```bash
mkdir asc-recovery && cd asc-recovery && git init
mkdir -p .claude/{commands,agents,skills} docs
```

## 0.2 Write `CLAUDE.md` — the project constitution

This file is loaded into every session. Keep it **short**; long CLAUDE.md files get
ignored. Everything else goes in `docs/` and gets referenced on demand.

```markdown
# ASC Underpayment Recovery Platform

## What this is
Detects claims where a payer paid less than the contract requires, explains why,
and produces a recovery worklist and appeal packet. Customers are ambulatory
surgery centers and the billing companies that serve them.

## Non-negotiable rules
1. NO REAL PHI in this repo, in tests, in logs, in fixtures, or in prompts. Ever.
   Synthetic data only. If you think you need real data, stop and ask.
2. Money is `Decimal`, never `float`. Never. Rounding is ROUND_HALF_UP, 2 places.
3. No LLM ever computes, adjusts, or infers a dollar amount. Money comes from
   deterministic rules against a contracted rate. LLMs draft prose only.
4. Every claim is priced against the contract version effective on its DATE OF
   SERVICE, never the current contract.
5. Every write to PHI-bearing tables goes through the audit log. No exceptions.
6. Logs, traces, and error messages must never contain PHI. Log claim IDs, not
   patient names. Assume every log line will be read by someone unauthorized.
7. Cloud-agnostic. Postgres, S3-compatible object storage, containers, Terraform.
   No proprietary managed service that has no equivalent on another cloud.
8. Multi-tenant. Every query is scoped by tenant_id. There is no global read.

## Stack
Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2.x · Alembic · Pydantic v2 ·
pytest · Docker · Terraform · OpenTelemetry

## Commands
- `make test` — full suite, must be green before any commit
- `make lint` — ruff + mypy strict
- `make eval` — golden-dataset accuracy check, must be 100% recall on known cases
- `make security` — bandit, pip-audit, secret scan

## Definition of done for any task
Tests written and passing · types clean · no PHI leak · audit entry where
applicable · docs/ updated if behaviour changed
```

## 0.3 Install guardrail hooks

Hooks are **deterministic code**, not suggestions. Use them for rules that must never
be violated. Put this in `.claude/settings.json` (run `/hooks` in Claude Code to
confirm the exact schema for your version):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "./scripts/hooks/block_phi.sh" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          { "type": "command", "command": "./scripts/hooks/check_money_types.sh" }
        ]
      }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "make test && make lint" } ] }
    ]
  }
}
```

`block_phi.sh` should reject any write containing patterns that look like real PHI
(SSN format, MRN patterns, real-looking DOB + name combinations) and any file path
under `data/real/`. `check_money_types.sh` should fail if a diff introduces `float`
in a module under `domain/`.

**Why hooks and not instructions:** an instruction in CLAUDE.md is advisory and will
eventually be missed across a long build. A hook is enforcement. Anything that would
be a compliance failure belongs in a hook.

## 0.4 Create slash commands

`.claude/commands/gate.md`:
```markdown
Run the full verification gate for the current phase:
1. `make test` — report failures with file:line
2. `make lint` — report violations
3. `make eval` — report recall, precision, cause accuracy
4. `make security` — report findings by severity
5. Grep the diff for: float in domain/, PHI in logs, unscoped queries missing tenant_id
6. State clearly: GATE PASSED or GATE FAILED, with the specific blocking items.
Do not fix anything. Only report.
```

`.claude/commands/phase.md`:
```markdown
Read docs/PHASES.md. Identify the current phase from git log and the state of the
codebase. Summarise: what is done, what remains in this phase, what the gate requires.
Then STOP and wait for instruction. Do not begin work.
```

## 0.5 Create the review subagent

`.claude/agents/adversarial-reviewer.md`:
```markdown
---
name: adversarial-reviewer
description: Reviews code for correctness, security and compliance defects. Use after any phase implementation, before the gate.
tools: Read, Grep, Glob, Bash
model: opus
---

You are a hostile reviewer on a healthcare payments system. Your job is to find
the defect that costs the customer money or triggers a breach notification.

Check in priority order:
1. PHI leakage — logs, traces, error messages, test fixtures, LLM prompts, URLs
2. Money errors — float arithmetic, rounding, sign errors, double-counting,
   missing idempotency on ingestion
3. Contract correctness — is the claim priced against the contract effective on
   the DATE OF SERVICE, not today's?
4. Tenant isolation — any query, any join, any cache key missing tenant scoping
5. Audit gaps — a PHI read or write with no audit entry
6. Auth — missing authz check, IDOR, MFA bypass

Report findings as: SEVERITY | file:line | what breaks | how to reproduce.
Do not fix. Do not soften. If you find nothing in a category, say so explicitly.
```

---

# PART 1 — THE PHASES

Each phase: **paste the prompt → let it plan → approve → build → run `/gate` → `/clear`.**

For every phase, start in **plan mode** (`Shift+Tab` twice, or `/plan`) so Claude
explores and proposes before writing. Approve the plan, then let it execute.

---

## PHASE 1 — Domain core (pure, no I/O)

> **Prompt:**
>
> Enter plan mode. Build the pure domain core for an ASC underpayment detection
> system. This layer has NO database, NO network, NO filesystem — pure functions and
> value objects only, so it is trivially testable.
>
> Build:
> 1. `domain/money.py` — a Money value type wrapping `Decimal`, ROUND_HALF_UP to 2
>    places. Addition, subtraction, multiplication by a rate, comparison. Must make
>    float arithmetic impossible by construction.
> 2. `domain/x835.py` — X12 835 parser. Handle ISA/GS/ST envelopes, BPR, TRN, N1
>    loops, CLP, NM1, SVC with composite procedure:modifier elements, CAS with
>    repeating triplets at both claim and service level, DTM, LQ, MIA/MOA, PLB.
>    Compute `allowed = charge - CO adjustments` and `paid = allowed - PR`.
>    Must handle: reversals (CLP02=22), denials (CLP02=4), secondary payer claims,
>    malformed segments, and files with mixed line endings.
> 3. `domain/contract.py` — fee schedule and payment rules: MPPR ranking, bilateral
>    modifier 50, assistant surgeon, implant carve-outs, percent-of-charge, case
>    rates, and per-payer rule overrides.
> 4. `domain/variance.py` — expected vs actual per line, with root-cause
>    classification and a human-readable evidence string for each finding.
>
> Constraints: Python 3.12, strict mypy, no external dependencies in this layer.
> Every public function fully type-annotated. Write tests first — I want the test
> file for each module before the implementation.
>
> Plan it, show me the plan, and wait for approval before writing code.

**Gate:** `make test` green · mypy strict clean · zero `float` anywhere in `domain/`
· parser handles all six malformed-input cases · 100% branch coverage on
`variance.py`.

---

## PHASE 2 — Eval harness and golden dataset

**This phase is why the product survives contact with reality. Do not skip it.**

> **Prompt:**
>
> Enter plan mode. Build an evaluation harness that measures detection accuracy
> against known answers, so every future change is regression-tested.
>
> 1. `evals/generator.py` — synthetic 835 generator that injects known defects and
>    records ground truth. Must cover at minimum: implant carve-out ignored,
>    MPPR applied to primary, bilateral modifier dropped, stale fee schedule,
>    duplicate line, reversal after payment, secondary-payer underpayment,
>    unpriced code, correct payment (control). Ground truth must record what was
>    ACTUALLY broken, not what was intended — some injections are no-ops on some
>    claim shapes, and mislabelling those makes a correct detector look wrong.
> 2. `evals/golden/` — a frozen set of ~500 synthetic claims with expected findings,
>    committed to the repo. This is the regression baseline.
> 3. `evals/run.py` — reports recall, precision, root-cause accuracy, and dollar
>    accuracy (sum of detected shortfall vs sum of injected shortfall).
> 4. `make eval` fails the build if recall < 100% or precision < 98% on golden.
>
> Recall matters more than precision here: a missed underpayment is money the
> customer never sees. But precision below ~98% wastes biller time, which is how
> tools like this get abandoned. Report both, always.
>
> Plan first. Wait for approval.

**Gate:** `make eval` runs clean · golden set committed · deliberately breaking one
rule in `variance.py` makes the eval fail (prove the harness actually catches
regressions).

---

## PHASE 3 — Persistence, tenancy, and effective-dated contracts

> **Prompt:**
>
> Enter plan mode. Build the persistence layer. Postgres 16, SQLAlchemy 2.x,
> Alembic migrations.
>
> Schema requirements:
> - `tenants` — every other table carries `tenant_id`, non-null, foreign-keyed.
> - **Row-Level Security enabled on every PHI table**, with policies keyed on a
>   session-local `app.tenant_id`. Application-level filtering alone is not
>   sufficient — one missed `WHERE` becomes a cross-tenant PHI breach.
> - `contracts` and `contract_versions` with `effective_from` / `effective_to`.
>   **Claims are priced against the version effective on the claim's DATE OF
>   SERVICE.** Rates change annually and claims arrive late; pricing against
>   "current" is silently wrong and unwinding it later means reprocessing history.
> - `fee_schedule_lines` — versioned with the contract.
> - `remittances` — with `file_hash` UNIQUE per tenant for **idempotency**. The same
>   835 delivered twice must never double-count recoverable dollars.
> - `claims`, `service_lines`, `adjustments`.
> - `findings` — detected variances, with the rule version that produced them.
> - `audit_log` — append-only: actor, action, resource, tenant, timestamp,
>   source IP, and whether PHI was accessed. No UPDATE or DELETE grant.
> - `phi_access_log` — separate, for minimum-necessary reporting.
>
> Also: soft-delete with retention policy (HIPAA requires 6 years of documentation),
> and a `rule_version` column on findings so results are reproducible after a rules
> change.
>
> Write migration tests that prove RLS actually blocks cross-tenant reads.
>
> Plan first. Wait for approval.

**Gate:** A test that authenticates as tenant A and attempts to read tenant B's
claims **must fail at the database level, with RLS as the only thing stopping it**
(disable app-level filtering to prove it) · re-ingesting an identical 835 creates
zero new findings · a claim dated last year prices against last year's contract.

---

## PHASE 4 — Security and PHI controls

> **Prompt:**
>
> Enter plan mode. Implement security controls to meet the 2026 HIPAA Security Rule,
> where encryption and MFA moved from "addressable" to **required**.
>
> 1. **Encryption** — AES-256 at rest (application-level for PHI columns, in addition
>    to disk encryption), TLS 1.2+ minimum in transit, enforced and tested.
> 2. **Key management** — envelope encryption with a KMS interface abstracted behind
>    a port so it works on AWS KMS, Azure Key Vault, GCP KMS, or Vault. Key rotation
>    without re-encrypting all data (rotate the KEK, not the DEK).
> 3. **AuthN** — OIDC, **MFA mandatory for every user and every session**. No
>    exceptions, no "internal user" bypass.
> 4. **AuthZ** — RBAC with minimum-necessary enforcement. Roles: viewer, biller,
>    admin, auditor. A biller sees only assigned worklists.
> 5. **Session** — short-lived access tokens, rotating refresh, forced re-auth for
>    PHI export, automatic logoff.
> 6. **PHI redaction middleware** — a log filter that strips names, DOB, MRN, SSN,
>    member ID and address from every log record, trace and exception. Then write a
>    test that deliberately logs a PHI-bearing object and asserts nothing sensitive
>    reaches the sink.
> 7. **Rate limiting** and account lockout.
> 8. **Secret management** — nothing in env files committed; interface for external
>    secret stores.
>
> Then produce `docs/SECURITY.md` documenting each control against its HIPAA
> citation, plus the written asset inventory and network map the 2026 rule now
> requires annually.
>
> Plan first. Wait for approval.

**Gate:** PHI redaction test passes · MFA cannot be bypassed by any code path ·
`make security` clean · no secrets in git history (`gitleaks` or equivalent over the
full history, not just HEAD).

---

## PHASE 5 — Ingestion pipeline

> **Prompt:**
>
> Enter plan mode. Build 835 ingestion.
>
> - Sources: file upload, SFTP poll, S3-compatible bucket drop. Abstract behind a
>   port so a new source is a new adapter, not a rewrite.
> - **Idempotency by content hash.** Same file twice = no duplicate findings.
> - Quarantine invalid files with a readable diagnostic; never silently drop.
> - Partial-batch handling: one bad claim must not fail the whole file.
> - Virus scan before processing.
> - **Reconciliation:** the BPR payment amount must equal the sum of claim payments
>   plus PLB adjustments. Flag mismatches — they usually mean a parsing bug or a
>   payer error, and both matter.
> - Handle reversals and takebacks (CLP02=22) by netting against prior findings.
> - Full audit entry per file: who uploaded, when, hash, outcome.
>
> Plan first. Wait for approval.

**Gate:** Same file ingested 3× produces identical totals · malformed file
quarantined with a useful message · BPR reconciliation catches an injected
mismatch · reversal correctly nets a prior finding to zero.

---

## PHASE 6 — API layer

> **Prompt:**
>
> Enter plan mode. FastAPI service exposing: upload remittance, list findings with
> filters, finding detail with full evidence chain, export worklist CSV, contract
> management, and audit log query for the auditor role.
>
> Requirements: OpenAPI spec generated · every endpoint tenant-scoped and
> authorization-tested · pagination on every list · **no PHI in URLs or query
> strings** (they land in access logs) · structured errors that never echo PHI ·
> request ID propagated into every log line and audit entry.
>
> Write an authorization test matrix: every role × every endpoint × own-tenant and
> other-tenant. Every cell asserted.
>
> Plan first. Wait for approval.

**Gate:** Full authz matrix green · OpenAPI validates · no endpoint returns another
tenant's data under any parameter manipulation.

---

## PHASE 7 — Recovery packet generation (the only place an LLM appears)

> **Prompt:**
>
> Enter plan mode. Generate the appeal/recovery packet a biller sends to the payer.
>
> **Hard boundary: the LLM drafts prose. It never computes, adjusts, or restates a
> dollar amount.** All figures are injected from the deterministic finding record via
> template substitution. After generation, run a validator that extracts every
> currency figure from the output and asserts each one exactly matches a value in the
> finding record. If any figure is unmatched, reject the draft and regenerate. Log
> the rejection.
>
> Also: no PHI in the prompt beyond the minimum necessary (procedure codes, dates,
> amounts — not names or member IDs; substitute placeholders and re-insert after
> generation). Support per-payer templates and appeal deadlines.
>
> **Track timely-filing windows.** Appeal rights expire, and an expired window is
> permanently lost money. Sort worklists by deadline proximity as well as dollar value.
>
> Include a human approval step before anything is marked ready to send. This is
> never fully autonomous.
>
> Plan first. Wait for approval.

**Gate:** Currency validator rejects a deliberately corrupted draft · no patient
identifiers in any LLM prompt (assert on captured prompts) · deadline calculation
correct across timezones and leap years.

---

## PHASE 8 — Observability and audit

> **Prompt:**
>
> Enter plan mode. OpenTelemetry traces, metrics, structured logs — all PHI-scrubbed
> at source, not at the sink.
>
> Business metrics: dollars detected, dollars recovered, recovery rate by cause,
> time-to-recovery, findings per remittance, eval scores over time.
> System metrics: ingestion latency, error rate, queue depth, LLM cost per packet.
> Alerts: ingestion failure, eval regression, auth anomaly, unusual PHI access
> volume, cross-tenant query attempt.
>
> Build the auditor-facing report: who accessed which patient's data, when, and why.
> This is the report you hand a customer's compliance officer, and having it ready
> shortens enterprise sales considerably.
>
> Plan first. Wait for approval.

**Gate:** Trace sampling captures no PHI (assert on exported spans) · audit report
reconstructs a full access history for a given claim.

---

## PHASE 9 — Cloud-agnostic deployment

> **Prompt:**
>
> Enter plan mode. Package for deployment on any cloud.
>
> - Multi-stage Dockerfile, non-root user, distroless or slim base, pinned digests.
> - Health, readiness and liveness endpoints.
> - Terraform modules with a provider-agnostic core: network, Postgres, object
>   storage, secrets, KMS, container runtime. Thin per-cloud adapters for AWS, Azure
>   and GCP. **Only use services covered by that provider's BAA** — AWS lists 166+
>   HIPAA-eligible services, Azure 80+, GCP ~100. If a service is not on the BAA
>   list, it cannot touch PHI.
> - Network segmentation (now explicitly required), private subnets, no public
>   object storage buckets — public-access-blocked at account level, not just bucket
>   level.
> - Automated encrypted backups with a **tested restore procedure**. An untested
>   backup is not a backup.
> - Zero-downtime migration strategy.
> - `docs/RUNBOOK.md`: deploy, rollback, restore, key rotation, incident response,
>   breach notification procedure with the 60-day HIPAA clock.
>
> Plan first. Wait for approval.

**Gate:** Deploys clean to at least two clouds from the same Terraform core ·
restore-from-backup rehearsed and timed · no PHI-touching service outside the BAA list.

---

## PHASE 10 — CI/CD and pre-production hardening

> **Prompt:**
>
> Enter plan mode. Pipeline: lint → type check → unit → integration → eval → security
> scan → container scan → IaC scan → deploy to staging → smoke test → manual gate →
> production.
>
> Add: dependency pinning with lockfile, SBOM generation, secret scanning on full
> history, DAST against staging, and a scheduled job for the **six-monthly
> vulnerability scan and annual penetration test** the 2026 rule now requires.
>
> Then: run the adversarial-reviewer subagent across the entire codebase and produce
> a prioritised defect list. Fix everything at HIGH or above before proceeding.
>
> Plan first. Wait for approval.

**Gate:** Pipeline green end to end · adversarial review shows zero HIGH/CRITICAL ·
staging environment fully functional on synthetic data.

---

## PHASE 11 — Real data readiness (the compliance gate)

**No code in this phase. Paperwork and process. Do not skip it — this is the phase
that determines whether you have a business or a lawsuit.**

Checklist before a single real 835 file touches your system:

- [ ] **BAA signed with your cloud provider**, covering every service you use
- [ ] **BAA signed with your customer** (you are their Business Associate)
- [ ] Written Security Risk Analysis completed and documented
- [ ] Written asset inventory and network map (now mandatory, annual review)
- [ ] Incident response plan with named owner and tested contact tree
- [ ] Breach notification procedure — 60-day clock, who decides, who notifies
- [ ] Workforce security training completed and evidenced
- [ ] Sanction policy for workforce violations
- [ ] Data retention and destruction schedule (6-year documentation minimum)
- [ ] Business continuity and disaster recovery plan, tested
- [ ] Subcontractor BAAs for every vendor touching PHI — **including your LLM
      provider**, and confirm zero-retention terms
- [ ] Cyber liability insurance
- [ ] Customer-facing security questionnaire answers prepared
- [ ] Penetration test completed by a third party
- [ ] Legal review of your contingency-fee contract structure

---

## PHASE 12 — First customer pilot

> **Prompt:**
>
> Enter plan mode. Build the pilot workflow: onboard one ASC, load their real fee
> schedule, ingest one historical quarter of 835 files, produce a findings report,
> and track which findings the customer actually recovers.
>
> Critically: build the **outcome feedback loop**. Every finding gets an outcome —
> recovered, denied, abandoned, expired. Feed those outcomes back into confidence
> scoring.
>
> That loop is the moat. Competitors can copy the rules; they cannot copy your
> record of which appeals actually won for which payer. After five customers it is
> the reason you win, so instrument it from day one rather than adding it later.
>
> Plan first. Wait for approval.

**Gate:** One real customer, one real quarter, findings validated by their biller,
recovery outcomes tracked.

---

# PART 2 — CLAUDE CODE TECHNIQUE PLAYBOOK

## When to use what

| Need | Use | Why |
|---|---|---|
| A rule that must NEVER be broken | **Hook** | Code enforcement, not advice |
| Always-on project context | **CLAUDE.md** | Loaded every session — keep it short |
| Reusable domain knowledge | **Skill** | Loaded on demand, doesn't eat context |
| Isolated parallel work | **Subagent** | Own context window, own tools |
| A prompt you repeat | **Slash command** | `/gate`, `/phase` |
| External system access | **MCP server** | Postgres, GitHub, monitoring |

## Plan mode
Start every phase with `Shift+Tab` twice. Claude explores and proposes before
writing. **Read the plan properly** — correcting a plan costs minutes, correcting an
implementation costs hours. Reject plans that skip tests or touch more than the phase
scope.

## The TDD loop (highest-leverage technique here)
```
1. "Write failing tests for X. Do not write the implementation."
2. Review the tests yourself. Are they testing the right thing?
3. "Now implement until the tests pass. Do not modify the tests."
4. /gate
```
This works especially well for this product because the domain is arithmetic with
knowable right answers.

## The self-critique loop
After any non-trivial implementation:
```
"Review what you just wrote as a hostile reviewer looking for the defect that
costs the customer money. Then fix what you find."
```
Then run the `adversarial-reviewer` subagent for an independent pass with clean
context. The second look catches what the author's context blinds them to.

## Subagent fan-out
For broad work, dispatch parallel subagents with isolated contexts:
```
"Spawn subagents in parallel:
 - one to audit every query for tenant scoping
 - one to audit every log statement for PHI
 - one to audit every money calculation for float
Consolidate findings into a single prioritised list."
```

## Context discipline
- `/clear` between phases, always
- If a session exceeds ~60% context, finish the current item, write state to
  `docs/PROGRESS.md`, then `/clear` and resume
- Reference files by path rather than pasting contents
- Never let one session span two phases

## Git worktrees for parallel phases
Phases 5–8 are largely independent:
```bash
git worktree add ../asc-api feature/api
git worktree add ../asc-observability feature/otel
```
Run a Claude Code session in each. Merge through PRs with the adversarial reviewer
as a required check.

---

# PART 3 — THINGS YOU WOULD HAVE MISSED

These are the gaps that appear six months in, when they are expensive. Every one is
already placed in a phase above.

**Domain correctness**
1. Effective-dated contracts — price against date of service, not today *(Phase 3)*
2. `Decimal` for money, never `float` *(Phase 1)* — the demo you have uses float; production cannot
3. Idempotent ingestion — same file twice must not double-count *(Phase 5)*
4. Reversals and takebacks netting against prior findings *(Phase 5)*
5. BPR-to-claims reconciliation catching parser bugs *(Phase 5)*
6. Secondary and tertiary payer coordination *(Phase 1)*
7. Timely-filing deadlines — expired appeal rights are permanently lost money *(Phase 7)*
8. `rule_version` on findings so results stay reproducible after a rules change *(Phase 3)*
9. Unpriced codes surfaced, never silently skipped *(Phase 1)*

**Security and compliance**
10. Row-Level Security, not just application filtering *(Phase 3)*
11. PHI scrubbed from logs at source *(Phase 4)*
12. No PHI in URLs or query strings *(Phase 6)*
13. Minimum-necessary PHI in LLM prompts, with zero-retention terms *(Phase 7, 11)*
14. MFA mandatory — no internal bypass *(Phase 4)*
15. Append-only audit log with no UPDATE/DELETE grant *(Phase 3)*
16. Key rotation without full re-encryption *(Phase 4)*
17. BAA with your LLM provider — routinely forgotten *(Phase 11)*
18. Six-year retention, with defensible destruction *(Phase 3, 11)*
19. Tested restore, not just backups *(Phase 9)*
20. Breach notification procedure with the 60-day clock *(Phase 9, 11)*

**Product and business**
21. Eval harness before features — regression protection *(Phase 2)*
22. Ground truth recorded as what actually broke, not what was intended *(Phase 2)*
23. Human approval before anything reaches a payer *(Phase 7)*
24. Outcome feedback loop — the actual moat *(Phase 12)*
25. Overpayment detection, so takebacks don't ambush the customer *(Phase 1)*
26. LLM cost controls per packet *(Phase 8)*
27. Precision tracked as hard as recall — false positives burn biller trust *(Phase 2)*

---

# PART 4 — THE ORDER, IN ONE LINE

**Domain core → evals → data model → security → ingestion → API → packets →
observability → deploy → CI/CD → compliance paperwork → pilot.**

Domain first because it is testable in isolation. Evals second because everything
after depends on knowing you haven't broken it. Security before ingestion because
retrofitting security onto a working pipeline is how breaches happen.

**Do not start Phase 12 until Phase 11 is fully signed.**

---

## Sources

- [Claude Code advanced best practices 2026](https://smartscope.blog/en/generative-ai/claude/claude-code-best-practices-advanced-2026/)
- [Claude Code hooks, subagents and power features](https://dev.to/vibehackers/claude-code-hooks-subagents-power-features-the-complete-guide-2026-c71)
- [Claude Code subagents production playbook](https://www.totalum.app/blog/claude-code-subagents-totalum)
- [HIPAA Security Rule changes 2026](https://medcurity.com/hipaa-security-rule-changes-2026/)
- [HIPAA Security Rule 2026: encryption and MFA](https://www.eunoiaconsultingco.com/blog/hipaa-security-rule-2026-mandatory-encryption-mfa)
- [5 HIPAA Security Rule changes in 2026](https://www.cbiz.com/insights/article/5-hipaa-security-rule-changes-in-2026-and-how-to-prepare)
- [HIPAA-compliant cloud architecture: AWS vs Azure vs GCP](https://www.tactionsoft.com/blog/hipaa-compliant-cloud-architecture-aws-azure-gcp/)
- [HIPAA-eligible cloud platforms](https://medcurity.com/hipaa-cloud-compliance/)
