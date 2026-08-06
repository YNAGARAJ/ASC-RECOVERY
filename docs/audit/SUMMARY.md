# Audit — Wave 1 Summary

Ten parallel, read-only subagents, each with a clean context, each auditing one
dimension of the codebase independently. No application code was modified by
any of them (one narrow, explicit exception: Agent 8 temporarily broke and
immediately reverted one line of `src/domain/variance.py` to prove the eval
harness catches a regression — confirmed via `git diff` showing no changes
before it finished). Full detail lives in `docs/audit/01-*.md` through
`docs/audit/10-*.md`.

## Finding counts by severity, per agent

| # | Area | File | CRITICAL | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|---|---|---|
| 1 | Spec conformance | `01-spec-conformance.md` | 0 | 4 | 6 | 5 | 15 |
| 2 | Hardcoded values | `02-hardcoded-values.md` | 0 | 0 | 4 | 5 | 9 |
| 3 | Money correctness | `03-money-correctness.md` | **1** | 2 | 2 | 2 | 7 |
| 4 | Domain correctness | `04-domain-correctness.md` | 0 | 2 | 2 | 3 | 7 |
| 5 | Security & PHI | `05-security-phi.md` | 0 | 5 | 5 | 3 | 13 |
| 6 | Tenant isolation | `06-tenant-isolation.md` | 0 | 1 | 2 | 1 | 4 |
| 7 | Wiring & integration | `07-wiring-integration.md` | 0 | 4 | 4 | 4 | 12 |
| 8 | Test quality | `08-test-quality.md` | 0 | 1 | 4 | 2 | 7 |
| 9 | Deployment portability | `09-deployment-portability.md` | 0 | 3 | 6 | 3 | 12 |
| 10 | Observability & LLM | `10-observability-llm.md` | 0 | 5 | 5 | 1 | 11 |
| | **Total** | | **1** | **27** | **40** | **29** | **97** |

Every agent explicitly confirmed at least one clean category with cited
evidence, per the "if a category is clean, say so" instruction — not every
category is a defect. Notably confirmed clean, with independent evidence:
zero `float` in any money path; date-of-service contract pricing (the
defect the master prompt calls most likely); the LLM money-validation
boundary's core two-stage check; PHI minimization in prompts (name/member id
never sent); `audit_log`'s real `REVOKE UPDATE, DELETE` grant; zero import
cycles anywhere in `src/`; and no hardcoded infrastructure credentials
anywhere in `src/`.

Three agents (1, 5, 7) independently found the same root defect — no
authentication path exists — from three different angles (spec conformance,
security/PHI, wiring). Three agents (5, 7, 1) also independently found rate
limiting and account lockout fully built and wired to zero routes. That
convergence, from agents with no shared context, is itself signal: these are
not edge-case readings, they're the same real gap seen from different
directions.

## The ten worst findings across the whole codebase

Ranked by actual blast radius (money correctness and deploy-blocking defects
first), not by which file happened to report them.

1. **[CRITICAL] Reversal netting can silently fail or mis-link, overstating recovered dollars on a real claim.** A reversal keyed on the reversal claim's own line layout drops the offsetting shortfall when line counts differ, or attaches to the wrong service line when the index exists but the procedure differs. `ingestion/plan.py:86-100`, `ingestion/apply.py:136-159`. (Agent 3)

2. **[HIGH] No authentication path exists anywhere in the running system.** `issue_session` — the sole point where MFA is checked — has zero callers outside tests; there is no login/enroll/verify route; the `users` table has no column to even store an MFA secret. "MFA cannot be bypassed" is true only because nothing performs authentication at all. Independently found by 3 agents. `security/session.py:68`, `security/mfa.py`, `api/routes/` (absent). (Agents 1, 5, 7)

3. **[HIGH] `PHI_ENCRYPTION_KEY` is required at startup but injected by neither cloud's Terraform.** A real `terraform apply` + deploy on AWS or Azure crash-loops the container before it serves a single request — neither `secrets_and_kms.tf` provisions this secret. `main.py:86`, both clouds' `container_runtime.tf`. (Agent 7)

4. **[HIGH] The deploy pipeline never runs `alembic upgrade head` or bootstraps the `asc_app` role.** `deploy.yml` can report green against a freshly-provisioned, empty-schema database — the smoke test only checks `/healthz` and `/readyz` (`SELECT 1`), both of which pass with no tables — while every real business endpoint 500s. `.github/workflows/deploy.yml`, `scripts/db/init_roles.sql`. (Agent 7)

5. **[HIGH] `main.py` never passes `instruments`/`tracer` into `PostgresRepository` — every ingestion metric and every trace silently no-ops in production.** `dollars_detected`, `findings_per_remittance`, `ingestion_latency`, `ingestion_failures` all record into a `NoOpMeterProvider`; the PHI-scrubbing span exporter never runs against a real span. Only LLM cost tracking is actually wired. `main.py:101`. (Agent 10)

6. **[HIGH] The LLM prompt sends claim identifiers to Anthropic in production, and its own docstring wrongly calls them "not PHI."** Payer claim control number and date of service go out as literal values (patient name/member id are correctly excluded). No BAA exists with Anthropic. `packets/prompt.py:63-65`, wired live via `main.py:95`. (Agent 5)

7. **[HIGH] The currency validator can be fooled by a real dollar figure attached to the wrong finding, and misses bare-integer hallucinations with no `$`/decimal point.** Both gaps sit directly on the CLAUDE.md rule 3 boundary ("no LLM ever computes or restates a dollar amount"). `packets/currency.py:23-37`, `packets/service.py:40-49`. (Agent 3)

8. **[HIGH] `BilateralConvention.TWO_LINE_SPLIT` is silently unimplemented, and the implant carve-out never actually fires on real ingestion because `invoice_cost` is always `None` on that path.** Implant lines — likely the highest-dollar recovery category for an ASC — always come back `UNPRICED_CODE` with zero shortfall instead of a computed one. `domain/contract.py:184-186`, `ingestion/plan.py:149-159`. (Agent 4)

9. **[HIGH] Rate limiting and account lockout are fully built and tested, wired to zero routes.** `enforce_rate_limit` has no callers anywhere; every endpoint, including PHI-decrypting reads, is unthrottled. Independently found by 3 agents. `api/rate_limit.py:16`, `security/rate_limit.py`. (Agents 5, 7, 1)

10. **[HIGH] Recording a finding outcome writes to a PHI-bearing table with no audit-log entry — a direct, literal violation of CLAUDE.md rule 5's "no exceptions."** This is Phase 12 code from earlier in this very session. The analogous `decide_packet` path two functions away does write an audit entry; `record_finding_outcome` does not. `api/repository.py:773-795`, `db/repository.py:565-589`. (Agent 10)

**Honorable mentions that didn't make the top 10 only because something else was worse**: SFTP/S3 ingestion sources fully built and completely unreachable in production (Agent 1); RLS policy coverage is hand-maintained per-table and proven only for `claims`, not derived from schema (Agent 6); AWS's ALB has no listener at all — the AWS deployment target is unreachable end to end, not just missing TLS (Agent 9); no real cloud KMS adapter exists for either cloud, so the app's own PHI envelope encryption never uses the provisioned, rotating cloud key (Agent 9); PHI log scrubbing is one `addFilter` call in the entire codebase rather than a structural guarantee (Agent 10); 4 of 5 alert evaluators have no runtime call site (Agent 10); all 28 DB-backed integration tests skip in every environment without a live Postgres, so a real schema/repository regression would stay green locally (Agent 8).

## What this means, plainly

Zero CRITICAL findings touch PHI confidentiality or tenant isolation directly
— no agent found a working cross-tenant leak, and the one CRITICAL found is a
money-correctness defect in reversal handling, not a breach. That is
consistent with this being a pre-Phase-11 system that has never touched real
PHI (CLAUDE.md rule 1) — these are real defects, but the blast radius today is
bounded by the fact that nothing real is at risk yet.

The dominant pattern across the whole audit, named independently by six of the
ten agents in one form or another, is **built-but-unwired**: MFA, rate
limiting/lockout, worklist ranking, SFTP/S3 ingestion, four of five alert
evaluators, and — critically — the observability instruments themselves are
all fully implemented, often well-tested in isolation, and simply never
connected to the code path that would make them real in production. This is a
different failure mode than "missing feature" — the engineering exists; the
composition root (`main.py`, the route layer, the deploy pipeline) just never
calls it. That is fixable fast (several of these are S/M effort, one-line
wiring changes) and is exactly what Wave 3's remediation loop should
prioritize once `docs/audit/REGISTER.md` (Wave 2) ranks it.

Next: Wave 2 — consolidate all ten files plus this summary into a single
deduplicated, ranked `docs/audit/REGISTER.md`.
