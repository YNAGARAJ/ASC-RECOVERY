# Audit — Wave 1, Agent 6: Tenant isolation

Read-only session. No application code was modified. **This environment has
no live Postgres and no Docker** (confirmed in `docs/audit/00-baseline.md`),
so nothing below was executed against a real database. Every statement about
RLS behaviour is a **code-reading verification of design**, not a run result.
Where a claim can only be proven by executing SQL against Postgres, that is
stated explicitly.

---

## The RLS-proof test — headline answer

**Does the test exist?** Yes. `tests/db/test_rls_tenant_isolation.py`.

**Is it well-designed?** Yes, for the one table it covers. Read in full it
does genuinely what the Phase 3 gate demands:

- `test_rls_blocks_cross_tenant_read_with_app_filtering_disabled` seeds two
  tenants, then — scoped to tenant A via `tenant_session` and connected as
  the **`asc_app` role** (not a superuser; see `tests/db/conftest.py:8-11`)
  — runs a **raw `SELECT patient_control_number FROM claims` with no
  `WHERE tenant_id = ...` clause at all** (line 55). Application-level
  filtering is deliberately absent, so RLS is the only thing that can keep
  tenant B's row out of the result. It asserts A's row is present and B's is
  absent (lines 57-58).
- It then proves the negative isn't a fluke: as the owner role it runs
  `ALTER TABLE claims NO FORCE ROW LEVEL SECURITY`, re-runs the query, and
  asserts **both** tenants' rows come back (lines 63-81). This distinguishes
  "RLS blocked it" from "the data was never there."
- `test_rls_blocks_cross_tenant_read_even_by_known_id` adds an IDOR check:
  knowing tenant B's exact claim `id` still returns nothing when scoped to
  tenant A (lines 84-103).

The design is sound and the fixture wiring is correct (the app connects as
`asc_app`, so the test is a real proof and not a table-owner false pass).

**Could it be executed here?** **No.** There is no live Postgres in this
environment; the test skips with an explicit `TEST_DATABASE_URL is not set`
message. The last real execution was Phase 10's CI run against Postgres 16,
which **predates Phase 12's schema changes** (migration 0005, the outcome
columns). Nothing has re-proven RLS since. Treat "RLS passes" as a claim
about an older commit.

**The gap that makes this a finding, not a clean pass:** the test proves RLS
for **`claims` only**. Ten other tables carry `tenant_id` and rely on RLS
(`contracts`, `contract_versions`, `fee_schedule_lines`, `remittances`,
`service_lines`, `adjustments`, `findings`, `audit_log`, `phi_access_log`,
and — critically — `recovery_packets`). None of them has any RLS assertion
anywhere in the suite (verified: the only `ROW LEVEL SECURITY` references in
`tests/` are the two `claims` lines above). `recovery_packets` is the
sharpest case: it holds the fully-rendered appeal letter *with the patient's
identifying details substituted back in* (`db/models.py:346-353`), and its
RLS policy is created in a **separate, later-authored migration (0004)**, not
in 0001 alongside the others. See HIGH finding below.

---

## What is genuinely clean (with evidence)

- **RLS is actually enabled, not decorative.** `alembic/versions/0001_initial_schema.py:80-92`
  runs, for each of 10 tables, `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
  SECURITY`, and `CREATE POLICY tenant_isolation ... USING (tenant_id =
  current_setting('app.tenant_id')::uuid) WITH CHECK (...)`. `FORCE` means the
  policy applies even to the table owner. `recovery_packets` gets the same
  four statements in `0004_...:41-50`. The policy is keyed on the
  session-local `app.tenant_id`, which `db/tenancy.py:37-40` sets per
  transaction via `set_config('app.tenant_id', :tenant_id, true)` (the `true`
  = transaction-local, so it never leaks across pooled connections). This is
  real database-enforced isolation, not app-layer filtering dressed up.
- **The `audit_log`/`phi_access_log` append-only grant** (`0001:76-78`) and
  `_MUTABLE_TABLES` never getting `DELETE` are consistent with the isolation
  model.
- **No client-supplied `tenant_id` anywhere.** `tenant_id` is only ever
  resolved server-side: `api/auth.py:65-78` looks up the user by the token's
  `subject` and reads `tenant_id` off the `User` row into `AuthContext`. Every
  route passes `ctx.tenant_id` (verified across all six route modules —
  `findings`, `contracts`, `audit`, `packets`, `remittances`). `api/schemas.py`
  has **zero** `tenant` fields (grep: no matches), so there is no path/query/body
  field to manipulate. This is the strongest part of the design and holds even
  on admin-style routes — there is no platform-superadmin path (`scripts/onboard_customer.py:5-11`
  is an operator-run script, not an endpoint).
- **The Phase 10 fix holds.** `db/repository.py:535-559`
  (`list_findings_by_payer_claim_control_number`) now filters
  `ClaimModel.tenant_id == tenant_id` explicitly in addition to RLS. Verified
  present. A `payer_claim_control_number` collision across tenants can no
  longer leak findings, even on an RLS-bypassed (superuser/owner) connection.
- **CSV export is tenant-scoped.** `api/routes/findings.py:66-108` derives its
  data from `repository.list_findings(ctx.tenant_id, ...)`; no cross-tenant path.
- **Log/audit aggregation is tenant-scoped.** `db/repository.py:908-982`
  (`get_claim_access_history`) filters `tenant_id` on every one of its four
  sub-queries (`findings`, `recovery_packets`, `audit_log`, `phi_access_log`).
  `merge_access_history` (`db/access_history.py`) is pure in-memory sorting
  over already-scoped rows.
- **Rate-limit cache key is tenant-scoped.** `api/rate_limit.py:17` keys on
  `f"{ctx.tenant_id}:{ctx.user_id}"`. (Note: this module is an orphan — never
  wired to any route per `00-inventory.md` — but the key itself is correct.)
- **No background jobs, pollers, or scheduled tasks exist** to be
  un-scoped. `list_findings_past_deadline_without_outcome` (`db/repository.py:622`)
  is the one function shaped like a sweep; it is tenant-scoped (requires and
  filters `tenant_id`) and, per grep, is called by nothing but its own test —
  there is no scheduler invoking it globally.
- **No cache layer** (Redis/memcached) exists in the codebase, so there are no
  additional cache keys to scope.
- **The two raw-session (non-`tenant_session`) call sites are safe:**
  `api/repository.py:446` (`ping`, `SELECT 1`, no PHI) and `:451`
  (`get_user_by_subject`, the deliberately-ungated bootstrap lookup, no PHI
  columns returned).

---

## Findings

### [HIGH] RLS enablement is proven for one table and hand-maintained per-table, with no guardrail against a new PHI table shipping without a policy
- **File:** `alembic/versions/0001_initial_schema.py:34-45` (the hardcoded `_TENANT_SCOPED_TABLES` tuple) · `tests/db/test_rls_tenant_isolation.py:45-103` (proves `claims` only) · `alembic/versions/0004_recovery_packets_and_timely_filing.py:32-50` (recovery_packets RLS lives in a separate migration)
- **What breaks:** The set of RLS-protected tables is a literal tuple in 0001 that does **not** auto-track `db/models.py`. Because 0001 builds the schema with `Base.metadata.create_all()` (line 69), any future PHI-bearing table added to the ORM is *created* automatically but gets **no RLS policy and no policy test** unless a human remembers to hand-write both — exactly the `recovery_packets` pattern (created by 0001's `create_all`, secured only later in 0004). Today `recovery_packets` — which stores the full appeal letter with patient name/member-id rendered in (`db/models.py:346-353`) — has its entire cross-tenant protection resting on a `CREATE POLICY` in a separately-authored migration that **no test exercises**. If that policy were ever dropped, mis-ordered, or omitted for the next such table, cross-tenant PHI reads would be possible and the suite would stay green. This is a latent CRITICAL-severity leak generator; rated HIGH because no leak exists *today* (0004 is present and correct), only an unguarded path to one.
- **Reproduce:** (requires live Postgres — cannot run here) 1) Add a new `tenant_id`-bearing model to `db/models.py`. 2) `alembic upgrade head` — the table is created via `create_all` with no policy. 3) Insert rows for two tenants, scope a `tenant_session` to tenant A, `SELECT * FROM new_table` with no `WHERE` — tenant B's rows return. No existing test fails. Equivalently: today, add a `recovery_packets` leg to `test_rls_tenant_isolation.py` and it would be the first thing ever to check 0004's policy.
- **Fix:** Add a data-driven test in `tests/db/` that queries `pg_class.relrowsecurity` / `pg_policies` and asserts **every** table with a `tenant_id` column has RLS enabled, forced, and a `tenant_isolation` policy — parametrized over the table list so a new table with no policy fails immediately. Separately, derive `_TENANT_SCOPED_TABLES` from `Base.metadata` (all tables carrying a `tenant_id` column, minus an explicit ungated allowlist of `{tenants, users}`) instead of hardcoding it, so RLS coverage tracks the models automatically.
- **Effort:** M

### [MEDIUM] `PostgresRepository.list_packets` omits the `tenant_id` guard its sibling methods apply, writing a PHI-access-log entry off an unguarded `session.get`
- **File:** `src/api/repository.py:740-754`
- **What breaks:** `list_packets` does `finding = session.get(FindingModel, finding_id)` with **no** `tenant_id` check, then writes a `phi_access_log` row using `finding.claim_id`. Its siblings `record_finding_outcome` (`:783`) and `decide_packet` (`:802`) both guard with `if ... row.tenant_id != tenant_id: return None`; `list_packets` does not. Under correctly-configured RLS this is safe (a cross-tenant `session.get` returns `None`, and `list_recovery_packets_for_finding` is tenant-filtered so returns `[]`). But if RLS were ever bypassed or misconfigured (the exact failure mode this defense-in-depth layer exists for), a caller passing another tenant's `finding_id` would cause a spurious `phi_access_log` entry to be written against another tenant's claim — an audit-integrity corruption, and an inconsistency that makes the codebase's own "we always double-check tenant_id in the app layer" claim untrue in one spot.
- **Reproduce:** Code inspection: compare `:744` (`session.get(FindingModel, finding_id)`, no guard) against `:782-783` and `:801-802` (same `session.get` followed by an explicit `tenant_id` mismatch return). Behavioural reproduction needs a live DB with RLS toggled off.
- **Fix:** After the `session.get`, add `if finding is not None and finding.tenant_id == tenant_id:` around the `write_phi_access_log` call (matching the sibling pattern), so the access-log write and the tenant check are consistent.
- **Effort:** S

### [MEDIUM] `db.repository.get_finding_detail` fans out to `session.get` / unfiltered joins with no explicit `tenant_id`, relying on RLS alone
- **File:** `src/db/repository.py:713-739`
- **What breaks:** After a correctly tenant-filtered fetch of the finding (`:717-720`), the function loads the claim and service line via `session.get(ClaimModel, finding.claim_id)` / `session.get(ServiceLineModel, finding.service_line_id)` (`:723-724`) and loads adjustments via `select(AdjustmentModel).where(AdjustmentModel.claim_id == finding.claim_id)` (`:730-732`) — **none of these carry a `tenant_id` predicate.** These are the rows returned as the PHI-bearing finding-detail response (claim numbers, encrypted patient fields, service line, adjustments). In practice this is not a leak even with RLS off, because every id is derived from a finding already confirmed to be in `tenant_id`, and FKs bind those rows to the same tenant. But the module's own docstring (`:11-14`) says app-level filtering is deliberately omitted because "RLS is the actual boundary" — which means for this PHI-assembly path there is **no** defense in depth: a single RLS misconfiguration is the only thing between a caller and the data. The same shape recurs in `_contract_version_to_domain` (`:157-165`, `fee_schedule_lines` unfiltered) and `api/repository.py::_lookup_payer_id` (`:386-398`, `session.get` on contract/version).
- **Reproduce:** Code inspection of `:723-732`; contrast with the explicit `tenant_id` predicate used in list/count queries elsewhere in the same module.
- **Fix:** Either (a) accept RLS as the single boundary but then make the `pg_policies` coverage test (HIGH finding above) a hard CI gate so RLS can never silently regress; or (b) add explicit `tenant_id` predicates to the adjustments query and replace the `session.get` calls with `tenant_id`-filtered `select(...).where(id == ..., tenant_id == ...)` for true defense in depth. (b) is the more conservative choice for PHI-bearing reads.
- **Effort:** M

### [LOW] The RLS control-leg leaves `claims` with `FORCE ROW LEVEL SECURITY` disabled if the test is interrupted
- **File:** `tests/db/test_rls_tenant_isolation.py:63-81`
- **What breaks:** The "prove it's really RLS" leg runs `ALTER TABLE claims NO FORCE ROW LEVEL SECURITY`, and restores `FORCE` in a `finally`. If the test process is killed (SIGKILL, CI timeout, OOM) between the two statements, the shared test database is left with `FORCE` off on `claims`, silently weakening the very protection the suite proves — and, since RLS is only asserted for `claims`, potentially masking a real regression on a shared/reused test DB. Low severity because it only affects a test database and only on abnormal termination.
- **Reproduce:** Inspect lines 63-81; the mutation is at DB level and outlives a killed process because it is committed (`:65`) before the `try`.
- **Fix:** Prefer running the control leg inside a transaction that is rolled back rather than an explicit `commit()` + `finally`-restore, or run this leg against a throwaway/ephemeral database, so an interrupted run cannot leave `FORCE` disabled.
- **Effort:** S

---

## Category-by-category verdict (nothing omitted)

| Category (from assignment) | Verdict |
|---|---|
| RLS enabled + policy per PHI table, keyed on `app.tenant_id` | **Yes, enabled & forced** on all 11 PHI tables (10 in 0001, `recovery_packets` in 0004). Real DB-level enforcement, not decoration. Systemic weakness: coverage is hand-maintained and proven for `claims` only — HIGH finding. |
| Every repository query double-filters `tenant_id` (defense in depth) | **Mostly yes.** Writes and list/count queries all carry explicit `tenant_id`. Exceptions rely on RLS alone via `session.get`/unfiltered joins whose ids derive from already-scoped rows — MEDIUM (`get_finding_detail`), MEDIUM (`list_packets` guard omission), plus LOW-grade `_contract_version_to_domain` / `_lookup_payer_id`. No genuine cross-tenant leak found today. |
| Phase 10 `list_findings_by_payer_claim_control_number` fix holds; look for others like it | **Fix confirmed present** (`:552-554`). No other same-shape bug found — the other cross-table joins (`list_historical_outcomes`, `list_findings_past_deadline_without_outcome`, `get_claim_access_history`) all filter `tenant_id` explicitly. |
| Cache keys / background jobs / scheduled tasks / CSV export / log aggregation tenant-scoped | **Yes / N-A.** CSV export, access-history aggregation, and the rate-limit key are all tenant-scoped. No cache layer and no scheduled jobs exist. Clean. |
| RLS-proof test exists, well-designed, disables app filtering, proves DB is the only block | **Exists and well-designed for `claims`**; genuinely disables app filtering and includes a control leg + IDOR leg. **Could not be executed here (no live Postgres).** Does not cover the other 10 tables — folded into the HIGH finding. |
| Any code path accepting a client-supplied `tenant_id` (incl. admin routes) | **None found.** `tenant_id` is always resolved from the authenticated user record; no schema/route/query/body field carries it. Strongest part of the design. Clean. |

## Finding count by severity

- CRITICAL: 0
- HIGH: 1
- MEDIUM: 2
- LOW: 1
- **Total: 4**

No genuine cross-tenant leak path was found in the code as it stands today.
The HIGH finding is a *latent* path to one (unguarded RLS coverage + a
single-table proof), not an active leak.
