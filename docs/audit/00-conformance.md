# Audit — Wave 0, Step 3: Phase conformance map

Read against `docs/MASTER-BUILD-PROMPT.md` directly, not against `docs/PHASES.md`'s
own self-reported status — a phase's own checkpoint file is not independent
evidence of itself. Where this map's verdict differs from `docs/PHASES.md`'s,
that's deliberate and is called out explicitly below. "A file existing is not a
deliverable being met" — several PASS-shaped phases below are marked PARTIAL for
exactly that reason.

Legend: **YES** genuinely met · **PARTIAL** some real evidence, some real gap ·
**NO** not met · **UNVERIFIABLE HERE** the claim may be true but this environment
(no Docker, no live Postgres, no `gh` CLI) cannot independently check it.

---

## Phase 1 — Domain core

| Deliverable | Exists? |
|---|---|
| `domain/money.py` (Money value type, float-impossible-by-construction) | Yes, 145 lines |
| `domain/x835.py` (X12 835 parser) | Yes, 533 lines |
| `domain/contract.py` (fee schedule + payment rules) | Yes, 267 lines |
| `domain/variance.py` (root-cause classification) | Yes, 178 lines |

**Gate:** `make test` green · mypy strict clean · zero `float` in `domain/` ·
parser handles all six malformed-input cases · 100% branch coverage on `variance.py`.

**Genuinely met: YES.** Verified this session: `grep -rn "float(" src/domain/`
and `: float\b` both empty. `tests/domain/test_x835.py` names and tests all six
required malformed cases explicitly by name (`reversal_835`, `denial_835`,
`secondary_payer_835`, `malformed_clp`, `malformed_missing_isa`, both
mixed-line-ending variants, `malformed_truncated_file`, `malformed_bare_single_line`
— more than six, all present). `domain/variance.py` at 100% branch coverage
(`00-baseline.md`). mypy/ruff clean across the whole repo.

---

## Phase 2 — Eval harness and golden dataset

| Deliverable | Exists? |
|---|---|
| `evals/generator.py` | Yes, 952 lines |
| `evals/golden/` (~500 synthetic claims) | Yes, `cases.py` 541 lines, 504 golden cases at run time |
| `evals/run.py` (recall/precision/root-cause/dollar accuracy) | Yes |
| `make eval` fails under threshold | Yes — gate literally encoded (`recall: 100%`, `precision: >=98%`) |

**Gate:** `make eval` clean · golden set committed · deliberately breaking one
rule in `variance.py` makes the eval fail.

**Genuinely met: PARTIAL.** `make eval` ran clean this session (504 cases, 100%
recall/precision/root-cause/dollar accuracy). The golden set is committed. But
**the third gate criterion — proving the harness actually catches a regression
by breaking a rule and watching it fail — has no recorded evidence anywhere**.
`docs/PHASES.md`'s Phase 2 entry is a single unadorned checkbox with no detail.
This is exactly Wave 1 Agent 8's assigned task; until it's actually performed
and the result recorded, this gate criterion is unverified, not failed.

---

## Phase 3 — Persistence, tenancy, and effective-dated contracts

| Deliverable | Exists? |
|---|---|
| `tenants`, RLS on every PHI table | Yes (`alembic/versions/0001_initial_schema.py`) |
| `contracts`/`contract_versions` with `effective_from`/`effective_to` | Yes |
| `fee_schedule_lines` versioned with contract | Yes |
| `remittances.file_hash` UNIQUE per tenant | Yes |
| `claims`, `service_lines`, `adjustments` | Yes |
| `findings` with `rule_version` | Yes |
| `audit_log` append-only, no UPDATE/DELETE grant | Yes, migration-enforced |
| `phi_access_log` | Yes |
| Soft-delete with retention policy | **Partial** — `deleted_at` columns exist on `Claim`/`User`, but there is
  no actual destruction/purge procedure anywhere in the codebase — `docs/PROGRESS.md`
  (Phase 11 checkpoint) already says this plainly: "this system currently has no
  hard-delete path for any PHI-bearing row." A column is not a retention policy. |

**Gate:** RLS blocks cross-tenant read at the DB level (app-filtering disabled)
· re-ingesting identical 835 = zero new findings · claim dated last year prices
against last year's contract.

**Genuinely met: UNVERIFIABLE HERE, PARTIAL by record.** All three gate tests
exist (`tests/db/test_rls_tenant_isolation.py`, `test_idempotent_remittance.py`,
`test_effective_dated_pricing.py`) and, per `docs/PHASES.md`, passed against a
real Postgres 16 during Phase 10's CI run. This session has no live database
and cannot re-run them — that CI run is the only evidence, and it predates
Phase 12's schema/repository changes. Nothing has re-proven RLS since.

---

## Phase 4 — Security and PHI controls

| Deliverable | Exists? |
|---|---|
| AES-256 at rest, TLS enforced | Envelope encryption yes (`security/encryption.py`); TLS is an infra-layer
  concern (`rds.force_ssl`, `sslmode=require`) added in Phase 10, not app-layer |
| KMS interface, portable, KEK rotation | Yes as a port (`security/kms.py`) with `LocalKMS` (test) and `EnvKMS`
  (stopgap, static-KEK-from-secret) adapters — **no real cloud KMS adapter exists for
  any cloud**, a named and repeatedly-documented gap, not silently missing |
| **AuthN: OIDC, MFA mandatory for every user and session, no exceptions** | **NO — see below** |
| AuthZ: RBAC, minimum-necessary, biller sees only assigned worklists | RBAC yes, fully matrix-tested. "Biller sees only *assigned* worklists"
  specifically — **no**, there is no assignment concept anywhere in the data model;
  a biller sees every finding in their tenant, not a subset assigned to them.
  `security/rbac.py`'s own docstring says as much: row-level worklist-assignment
  scoping "doesn't exist until Phase 6/7 build" it — it still doesn't exist. |
| Session: short-lived tokens, rotating refresh, forced re-auth for PHI export, auto-logoff | Tokens + refresh + `require_recent_auth` (step-up) all implemented and tested
  in `security/session.py` — **but see Phase 6 below: nothing calls `require_recent_auth`
  from any actual PHI-export route**, so the step-up mechanism is built and unused,
  same pattern as the rate-limit and MFA gaps. |
| PHI redaction middleware, tested | Yes, `security/redaction.py` + test that logs a PHI-bearing object and
  asserts none of it reaches the sink |
| Rate limiting + account lockout | **Built, fully tested in isolation, wired to nothing** — see below |
| Secret management, external store interface | Yes, `security/secrets.py` port + `EnvSecretStore` adapter |

**Gate:** PHI redaction test passes · **MFA cannot be bypassed by any code
path** · `make security` clean · no secrets in full git history.

**Genuinely met: PARTIAL, and the MFA line is the sharpest finding in this
whole map.** `security/mfa.py` (`generate_enrollment_secret`, `provisioning_uri`,
`verify_code`) is fully implemented and unit-tested — but **`00-inventory.md`
confirms it is imported by nothing except its own test file.** There is no
enrollment route, no verification route, no login endpoint of any kind in
`api/routes/`. The literal claim "MFA cannot be bypassed by any code path" is
*vacuously* true today, in the worst possible way: there is no code path that
performs authentication at all in this running system. Every test and every
real request mints a JWT directly via `issue_session(..., mfa_verified=True)`,
a value nothing ever independently checks against a real MFA verification.
This is not a subtle gap — "MFA mandatory for every user and every session, no
exceptions" is a literal, named 2026 HIPAA Security Rule requirement in the
master prompt, and today it is unenforceable because there is nothing to
enforce it *on*. Same shape, lower severity: rate limiting
(`api/rate_limit.py::enforce_rate_limit`) is a zero-importer orphan module —
built, tested, never `Depends()`-attached to any route. `docs/compliance/
SECURITY-RISK-ANALYSIS.md` already names this as the single highest residual
risk in prose; this audit now has the exact file:line. `make security` ran
clean this session (bandit, pip-audit); gitleaks over full history could not
be run here (not installed) — its last real run was Phase 10's CI, before
Phases 11/12 added new files.

---

## Phase 5 — Ingestion pipeline

| Deliverable | Exists? |
|---|---|
| Sources: upload, SFTP poll, S3 bucket drop, behind a port | **Partial — see below** |
| Idempotency by content hash | Yes, `record_remittance_if_new` UNIQUE `(tenant_id, file_hash)` |
| Quarantine invalid files, readable diagnostic | Yes |
| Partial-batch handling | Yes — malformed lines dropped without failing the batch |
| Virus scan before processing | Yes (`ingestion/virus_scan.py`, EICAR-tested) |
| BPR reconciliation | Yes, `ingestion/reconcile.py` |
| Reversal/takeback netting | Yes, `ingestion/plan.py` |
| Full audit entry per file | Yes |

**Gate:** same file 3× = identical totals · malformed file quarantined with
useful message · BPR reconciliation catches injected mismatch · reversal nets
prior finding to zero.

**Genuinely met: PARTIAL.** The gate's four specific tests all exist and pass
(pure-logic ones verified again this session; the DB-backed idempotency one is
in the same UNVERIFIABLE-HERE bucket as Phase 3). But the deliverable list
promised three sources "behind a port so a new source is a new adapter, not a
rewrite" — **`00-inventory.md` confirms `ingestion/sources.py`'s
`SFTPPollSource`/`S3PollSource`/`UploadSource` classes are imported by nothing
except their own test file.** `ingestion.pipeline.ingest_file` takes raw
`content: bytes` directly; the only real route (`POST /remittances`, file
upload) never goes through `UploadSource` either. SFTP and S3 ingestion are
fully built and unit-tested in isolation, and **completely unreachable** in the
running system — no poller, no scheduled job, nothing ever constructs them
outside their test. Two of the three required "sources" don't actually exist
as far as any real deployment of this system is concerned.

---

## Phase 6 — API layer

| Deliverable | Exists? |
|---|---|
| Upload remittance, list/detail findings, export CSV, contract mgmt, audit log query | Yes, all present as routes |
| OpenAPI spec generated | Yes, validated (`tests/api/test_openapi.py`) |
| Every endpoint tenant-scoped and authz-tested | Yes — full matrix, `test_authz_matrix.py`, 52 cases post-Phase-12 |
| Pagination on every list | Yes |
| No PHI in URLs/query strings | Yes — filters are UUID/enum/date/dollar only, checked structurally |
| Structured errors, never echo PHI | Yes, tested (`test_error_redaction.py`) — though the `ErrorOut` schema
  meant to shape these responses is itself unused dead code (see `00-inventory.md`);
  the actual handler builds a plain dict of the same shape, so behavior is correct,
  the model just isn't wired to it |
| Request ID propagated into logs and audit | Yes |

**Gate:** full authz matrix green · OpenAPI validates · no endpoint leaks
another tenant's data under parameter manipulation.

**Genuinely met: YES**, on the letter of this phase's own gate. Worth
restating from Phase 4, since it surfaces here structurally: **there is still
no login/credential/OIDC endpoint anywhere** — a real user cannot authenticate
against this system today by any means. This was an explicit, named scope
decision at the time ("Phase 4 never built real credential verification to
wire one to"), not an oversight, but six phases later it's still true and is
the actual root cause of the Phase 4 MFA finding above.

---

## Phase 7 — Recovery packet generation

| Deliverable | Exists? |
|---|---|
| Currency validator rejects unmatched figures, regenerates | Yes, tested with a deliberately corrupted draft |
| No PHI beyond minimum necessary in prompts, placeholder substitution | Yes, tested against distinctive names |
| Per-payer templates | Yes, `packets/templates.py` |
| Timely-filing deadline tracking | Yes, `domain/deadlines.py` |
| **Worklists sorted by deadline proximity and dollar value** | **Built, never called — see below** |
| Human approval step, never autonomous | Yes — `draft -> approved|rejected`, explicit human actor required |

**Gate:** currency validator rejects corrupted draft · no patient identifiers
in any LLM prompt · deadline math correct across timezones/leap years.

**Genuinely met: PARTIAL.** All three literal gate tests pass. But
`packets/worklist.py::rank_worklist` (deadline-then-dollar-value ranking) is,
per `00-inventory.md`, imported by nothing except its own test —
`GET /findings` returns rows in plain `created_at DESC` order with no
ranking applied anywhere. The deliverable explicitly promised is not reachable
by a real biller through the API today.

---

## Phase 8 — Observability and audit

| Deliverable | Exists? |
|---|---|
| Traces/metrics/logs, PHI-scrubbed at source | Yes, `PHIScrubbingSpanExporter` wraps any real exporter; tested |
| Business metrics: dollars detected, **dollars recovered**, **recovery rate by cause**, **time-to-recovery**, findings/remittance, eval scores | **Partial — see below** |
| System metrics: ingestion latency, error rate, queue depth, LLM cost/packet | `queue_depth` is a documented stub (always 0, no queue exists to measure);
  the rest are real and wired |
| Alerts: ingestion failure, eval regression, auth anomaly, unusual PHI access, cross-tenant probe | Yes, 5 pure evaluators — detection logic only, no real paging integration
  (explicitly deferred to Phase 9/10, itself still not done — see Phase 9) |
| Auditor access-history report | Yes, `GET /claims/{id}/access-history`, tenant-isolated, tested |

**Gate:** trace sampling captures no PHI · audit report reconstructs full
access history for a claim.

**Genuinely met: YES on the literal gate**, but the phase's own deliverable
list is **still not complete, and this is now a sharper finding than when
`docs/PHASES.md` first wrote it down.** Phase 8's entry explicitly deferred
`dollars_recovered`/`recovery_rate_by_cause`/`time_to_recovery` because "no
outcome-tracking data model exists anywhere in this codebase" — true when
written. **Phase 12 has since built exactly that data model**
(`domain/outcomes.py`, `findings.outcome`/`amount_recovered`), and
`observability/metrics.py` was not touched in Phase 12 — verified this
session: `grep` for `dollars_recovered`/`recovery_rate_by_cause`/
`time_to_recovery` in `metrics.py` finds only the module's own docstring
explaining they're *not* built, unchanged since Phase 8. The blocking
dependency this gap was originally excused by no longer exists, and the gap
remains anyway.

---

## Phase 9 — Cloud-agnostic deployment

**Genuinely met: NO, and `docs/PHASES.md` already says so plainly** — this map
agrees rather than softening it. No Docker CLI exists in this environment at
all (confirmed again this session: `docker: command not found`), no cloud
account, no `terraform validate`/`apply` ever run against real AWS/Azure. The
Terraform HCL and Dockerfile are written and manually reviewed but the gate
("deploys clean to at least two clouds... restore rehearsed and timed") has
never been attempted, let alone met.

---

## Phase 10 — CI/CD and pre-production hardening

**Genuinely met: UNVERIFIABLE HERE for anything after the last recorded CI
run.** `docs/PHASES.md` records a real, detailed green CI run (RLS,
idempotency, effective-dated pricing, packet approval, access history all
confirmed against real Postgres 16, plus a documented 15-item "CI debugging
round"). This environment has no `gh` CLI configured and cannot check whether
`ci.yml` has actually run — let alone gone green — against the commit Phase 12
just pushed (`244463d`). **Nobody has confirmed CI is still green after Phase
12's schema/API changes.** Treat "the pipeline is green" as a claim about a
past commit, not this one, until checked.

---

## Phase 11 — Real data readiness (the compliance gate)

**Genuinely met: NO**, and this is not in dispute anywhere — `docs/PHASES.md`,
`docs/PROGRESS.md`, and `docs/compliance/README.md` all already say this
plainly and consistently. 7 of 15 checklist items have a real drafted
document; 0 of 15 read DONE with the actual external evidence the gate
requires (signed BAAs, purchased insurance, a completed pentest engagement,
run workforce training, a rehearsed tabletop exercise). Phase 12 was started
anyway, by explicit user direction overriding the master prompt's own
sequencing rule ("do not start Phase 12 until Phase 11 is fully signed") —
recorded honestly in `docs/PHASES.md`'s Phase 12 entry, not hidden.

---

## Phase 12 — First customer pilot

| Deliverable | Exists? |
|---|---|
| Onboard one ASC, load fee schedule, ingest a quarter of 835s, findings report | Built as a synthetic demonstration (`scripts/onboard_customer.py`,
  `tests/api/test_pilot_workflow_live_db.py`) — **there is no real customer or real
  835 data anywhere**, honestly labeled as such throughout |
| Outcome feedback loop: recovered/denied/abandoned/expired, feeding confidence scoring | Yes — `domain/outcomes.py`, `POST /findings/{id}/outcome`,
  `confidence_score` on finding detail |

**Gate (as literally written):** "One real customer, one real quarter,
findings validated by their biller, recovery outcomes tracked."

**Genuinely met: NO, and not close** — taken literally, this gate requires an
actual paying customer and real 835 data, which cannot exist before Phase 11
closes (`CLAUDE.md` rule 1, non-negotiable). What was actually built is the
*mechanism* a real pilot would need — the code-complete status
`docs/PHASES.md` claims for Phase 12 is accurate on its own narrower terms
(the engineering half), but should not be conflated with this gate, which is
about a real pilot happening, not code existing that could support one. In
addition, per this audit: the four DB-backed test files Phase 12 added have
never run against a live Postgres (same UNVERIFIABLE-HERE status as Phases 3/5),
and `db/repository.py::get_contract_by_payer_id` — written for this system's
existing contract lookup, not new to Phase 12 — has zero call sites despite
its own docstring's claimed purpose (`00-inventory.md`), worth a closer look
in Wave 1 to determine if that's a leftover or a real wiring gap in the
packet-generation deadline path.

---

## Summary table

| Phase | Verdict | One-line reason |
|---|---|---|
| 1 | **YES** | All gate criteria independently re-verified this session |
| 2 | PARTIAL | `make eval` clean, but "prove the harness catches a regression" never demonstrated |
| 3 | UNVERIFIABLE HERE (PARTIAL by record) | No live DB in this environment; last real proof predates Phase 12 |
| 4 | PARTIAL, sharpest finding | MFA fully built, zero enforcement path; rate limiting/lockout/step-up all built, zero wiring |
| 5 | PARTIAL | Core ingestion gate met; 2 of 3 required "sources" (SFTP, S3) unreachable in production |
| 6 | YES | Authz matrix, OpenAPI, no-PHI-in-URLs all hold; root MFA/login gap surfaces here structurally |
| 7 | PARTIAL | Currency/PHI/deadline gates met; worklist ranking built, never applied to any endpoint |
| 8 | PARTIAL, newly sharper | Trace/audit gates met; 3 of 7 named business metrics still missing, and Phase 12 just removed the excuse |
| 9 | NO | No Docker, no cloud account, nothing deployed anywhere |
| 10 | UNVERIFIABLE HERE | Last known-green run predates Phase 12's push; not re-checked |
| 11 | NO | 0/15 items closed with real evidence; started Phase 12 anyway, by explicit override |
| 12 | NO (as literally written) | No real customer/data exists yet; the engineering mechanism is real and code-complete |
